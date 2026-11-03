"""The simulation engine: one object that owns the whole simulated world.

Both the web application and the CLI drive this class, so there is exactly one
implementation of the blockchain network.

Determinism
-----------
* Time is simulated: an integer number of milliseconds advanced only by ``advance``.
* Randomness (latency jitter) comes from one ``random.Random`` seeded from ``seed``.
* Wallet keys are derived from ``(seed, name)``; mining nonces start at 0.
* No code in the engine reads the wall clock or iterates unordered sets.
* Every state-changing call is recorded in a command log with its simulated
  timestamp. ``Simulation.from_log`` replays a log and reproduces the exact same
  ``state_digest()``.
"""
from __future__ import annotations

import copy
import dataclasses
import functools
import json
import random
import threading
from typing import Any, Dict, List, Optional

from .block import Block
from .consensus import CONSENSUS_ASSUMPTIONS, ConsensusParams, make_coinbase, make_genesis
from .events import EventLog, EventType
from .hashing import sha256_hex
from .message import MessageType
from .miner import proof_of_work
from .network import Network
from .scheduler import Scheduler
from .transaction import Transaction
from .wallet import Wallet

LOG_VERSION = 1

DEFAULT_CONFIG: Dict[str, Any] = {
    "seed": 42,
    "initial_nodes": 5,
    "topology": "ring",
    "difficulty": 10,
    "max_difficulty": 22,
    "block_reward": 50,
    "max_txs_per_block": 20,
    "hashrate": 1000,
    "mining_tick_ms": 100,
    "latency_ms": 400,
    "latency_jitter_ms": 100,
    "initial_balance": 1000,
    "accounts": ["alice", "bob", "carol", "dave", "erin"],
    "speed": 2.0,
}

INVALID_BLOCK_KINDS = {
    "bad_pow": "valid structure, but the hash does not meet the difficulty target",
    "bad_hash": "contents changed after mining, so the stored hash no longer matches",
    "inflated_reward": "properly mined, but the miner pays itself more than the block reward",
    "overspend": "properly mined, but contains a transaction that spends more than the sender owns",
    "forged_signature": "properly mined, but contains a transaction signed with the wrong private key",
}


class SimulationError(ValueError):
    """A request that the simulation cannot honour (bad id, bad value, ...)."""


def command(fn):
    """Mark a state-changing method: lock it and record it in the replay log."""

    @functools.wraps(fn)
    def wrapper(self: "Simulation", *args, **kwargs):
        with self.lock:
            record = self._depth == 0 and not self._advancing and not self._replaying
            self._depth += 1
            try:
                result = fn(self, *args, **kwargs)
            finally:
                self._depth -= 1
            if record:
                self.command_log.append({"t": self.now_ms, "op": fn.__name__,
                                         "args": copy.deepcopy(list(args)), "kwargs": copy.deepcopy(kwargs)})
            return result

    return wrapper


def merge_config(overrides: Optional[dict]) -> dict:
    cfg = dict(DEFAULT_CONFIG)
    unknown = set(overrides or {}) - set(DEFAULT_CONFIG)
    if unknown:
        raise SimulationError(f"unknown config key(s): {', '.join(sorted(unknown))}")
    cfg.update(overrides or {})
    return cfg


class Simulation:
    def __init__(self, config: Optional[dict] = None, seed: Optional[int] = None,
                 nodes: Optional[int] = None, topology: Optional[str] = None,
                 events: Optional[EventLog] = None):
        self.lock = threading.RLock()
        self.config = merge_config(config)
        self.events = events or EventLog()
        self.running = True
        self.speed = float(self.config["speed"])
        self.generation = 0
        self._depth = 0
        self._advancing = False
        self._replaying = False
        self.scenario = None
        self._initialize(seed, nodes, topology)

    # ------------------------------------------------------------------
    # construction
    # ------------------------------------------------------------------
    def _initialize(self, seed, nodes, topology, scenario: Optional[str] = None) -> None:
        cfg = self.config
        self.seed = int(cfg["seed"] if seed is None else seed)
        n_nodes = int(cfg["initial_nodes"] if nodes is None else nodes)
        topo = topology or cfg["topology"]
        if not 0 <= n_nodes <= 50:
            raise SimulationError("node count must be between 0 and 50")
        self.generation += 1
        self.scheduler = Scheduler(on_error=self._on_scheduler_error)
        self.wallets: Dict[str, Wallet] = {}
        self._names: Dict[str, str] = {}
        accounts = [self._wallet(name) for name in cfg["accounts"]]
        self.genesis = make_genesis({w.address: int(cfg["initial_balance"]) for w in accounts})
        self.params = ConsensusParams(int(cfg["block_reward"]), int(cfg["max_txs_per_block"]),
                                      [(0, int(cfg["difficulty"]))])
        rng = random.Random(int(sha256_hex(f"network|{self.seed}"), 16))
        self.network = Network(self.scheduler, self.events, self.params, self.genesis, rng,
                               lambda node_id: self._wallet(f"miner-{node_id}").address,
                               int(cfg["latency_ms"]), int(cfg["latency_jitter_ms"]),
                               int(cfg["hashrate"]), int(cfg["mining_tick_ms"]))
        self.command_log: List[dict] = []
        self.header = {"seed": self.seed, "initial_nodes": n_nodes, "topology": topo, "scenario": scenario}
        self.scenario = None
        self.events.clear()
        ev = self.events.emit(0, EventType.SIM_RESET,
                              f"simulation reset (seed {self.seed}, genesis {self.genesis.hash[:8]})")
        self.reset_event_id = ev.id
        for _ in range(n_nodes):
            self.network.add_node()
        self.network.build_topology(topo)

    def _wallet(self, name: str) -> Wallet:
        if name not in self.wallets:
            w = Wallet(name, self.seed)
            self.wallets[name] = w
            self._names[w.address] = name[6:] if name.startswith("miner-") else name
        return self.wallets[name]

    def _on_scheduler_error(self, exc: Exception) -> None:
        self.events.emit(self.scheduler.now_ms, EventType.ERROR, f"internal error: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------
    # small helpers
    # ------------------------------------------------------------------
    @property
    def now_ms(self) -> int:
        return self.scheduler.now_ms

    def name_of(self, address: str) -> str:
        return self._names.get(address, address[:10] + "…" if address else "")

    def node(self, node_id: str):
        n = self.network.nodes.get(node_id)
        if n is None:
            raise SimulationError(f"unknown node '{node_id}'")
        return n

    def canonical_node(self):
        """The node holding the longest chain (ties: lowest id): the explorer's reference."""
        nodes = self.network.nodes
        if not nodes:
            return None
        return min(nodes.values(), key=lambda n: (-n.blockchain.height, n.id))

    # ------------------------------------------------------------------
    # time
    # ------------------------------------------------------------------
    def advance(self, ms: int) -> int:
        """Advance simulated time by ``ms`` milliseconds, running all due events."""
        if ms < 0:
            raise SimulationError("cannot advance by a negative amount")
        with self.lock:
            self._advancing = True
            try:
                return self.scheduler.run_until(self.now_ms + int(ms))
            finally:
                self._advancing = False

    def pause(self) -> None:
        with self.lock:
            if self.running:
                self.running = False
                self.events.emit(self.now_ms, EventType.SIM_PAUSED, "simulation paused")

    def resume(self) -> None:
        with self.lock:
            if not self.running:
                self.running = True
                self.events.emit(self.now_ms, EventType.SIM_RESUMED, "simulation resumed")

    def set_speed(self, speed: float) -> None:
        if not 0.1 <= float(speed) <= 100:
            raise SimulationError("speed must be between 0.1 and 100 simulated seconds per real second")
        self.speed = float(speed)

    def reset(self, seed: Optional[int] = None, nodes: Optional[int] = None, topology: Optional[str] = None) -> None:
        with self.lock:
            self._initialize(seed, nodes, topology)

    # ------------------------------------------------------------------
    # commands (recorded for replay)
    # ------------------------------------------------------------------
    @command
    def add_node(self, hashrate: Optional[int] = None, auto_connect: int = 2) -> str:
        if len(self.network.nodes) >= 50:
            raise SimulationError("the simulator supports at most 50 nodes")
        if hashrate is not None and not 1 <= int(hashrate) <= 200000:
            raise SimulationError("hashrate must be between 1 and 200000 attempts per simulated second")
        existing = sorted(self.network.nodes.values(), key=lambda n: (len(self.network.neighbours(n.id)), n.id))
        node = self.network.add_node(hashrate=int(hashrate) if hashrate else None)
        for other in existing[:max(0, int(auto_connect))]:
            self.network.connect(node.id, other.id)
        return node.id

    @command
    def remove_node(self, node_id: str) -> None:
        self.node(node_id)
        self.network.remove_node(node_id)

    @command
    def connect(self, a: str, b: str) -> None:
        self._net_op(self.network.connect, a, b)

    @command
    def disconnect(self, a: str, b: str) -> None:
        self._net_op(self.network.disconnect, a, b)

    def _net_op(self, fn, *args) -> Any:
        try:
            return fn(*args)
        except ValueError as exc:
            raise SimulationError(str(exc)) from exc

    @command
    def set_node_online(self, node_id: str, online: bool) -> None:
        self.node(node_id)
        self.network.set_online(node_id, bool(online))

    @command
    def partition(self, groups: List[List[str]]) -> int:
        if len(groups) < 2:
            raise SimulationError("a partition needs at least two groups")
        return len(self._net_op(self.network.partition, groups))

    @command
    def heal(self) -> int:
        return len(self._net_op(self.network.heal))

    @command
    def create_transaction(self, sender: str, receiver: str, amount: int, node_id: str,
                           nonce: Optional[int] = None) -> dict:
        node = self.node(node_id)
        wallet = self.wallets.get(sender)
        if wallet is None or sender not in self.config["accounts"]:
            raise SimulationError(f"unknown sender account '{sender}'")
        if receiver in self.wallets and receiver in self.config["accounts"]:
            receiver_addr = self.wallets[receiver].address
        elif receiver in self._names:
            receiver_addr = receiver
        else:
            raise SimulationError(f"unknown receiver '{receiver}'")
        if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
            raise SimulationError("amount must be a positive whole number")
        if nonce is None:
            pending = sum(1 for t in node.mempool.all() if t.sender == wallet.address)
            nonce = node.blockchain.tip_state.next_nonce(wallet.address) + pending
        tx = wallet.create_transaction(receiver_addr, amount, int(nonce), self.now_ms)
        ok, reason = node.submit_transaction(tx)
        return {"accepted": ok, "reason": reason, "tx": self._tx_view(tx)}

    @command
    def start_mining(self, node_id: str, blocks: Optional[int] = None) -> None:
        if blocks is not None and (not isinstance(blocks, int) or blocks < 1):
            raise SimulationError("blocks must be a positive integer")
        targets = sorted(self.network.nodes) if node_id == "all" else [self.node(node_id).id]
        for nid in targets:
            node = self.network.nodes[nid]
            if node.status != "online":
                if node_id != "all":
                    raise SimulationError(f"{nid} is offline")
                continue
            node.miner.start(blocks)

    @command
    def stop_mining(self, node_id: str) -> None:
        targets = sorted(self.network.nodes) if node_id == "all" else [self.node(node_id).id]
        for nid in targets:
            self.network.nodes[nid].miner.stop()

    @command
    def mine_one(self, node_id: str) -> None:
        self.start_mining(node_id, 1)

    @command
    def set_difficulty(self, bits: int) -> int:
        if isinstance(bits, bool) or not isinstance(bits, int) or not 1 <= bits <= self.config["max_difficulty"]:
            raise SimulationError(f"difficulty must be a whole number between 1 and {self.config['max_difficulty']} (leading zero bits)")
        height = max((n.blockchain.height for n in self.network.nodes.values()), default=0)
        self.params.set_difficulty(height + 1, bits)
        self.events.emit(self.now_ms, EventType.DIFFICULTY_CHANGED,
                         f"difficulty set to {bits} bits (~{2 ** bits} attempts per block), effective from height {height + 1}",
                         None, {"bits": bits, "from_height": height + 1})
        for nid in sorted(self.network.nodes):
            self.network.nodes[nid].miner.refresh(force=True)
        return height + 1

    @command
    def set_latency(self, latency_ms: int, jitter_ms: Optional[int] = None) -> None:
        jitter = self.network.jitter_ms if jitter_ms is None else int(jitter_ms)
        if not 1 <= int(latency_ms) <= 60000 or not 0 <= jitter <= int(latency_ms):
            raise SimulationError("latency must be 1-60000 ms and jitter between 0 and the latency")
        self.network.latency_ms, self.network.jitter_ms = int(latency_ms), jitter
        self.events.emit(self.now_ms, EventType.LATENCY_CHANGED, f"network latency set to {latency_ms} ms (jitter ±{jitter} ms)")

    @command
    def set_hashrate(self, node_id: str, hashrate: int) -> None:
        if not isinstance(hashrate, int) or not 1 <= hashrate <= 200000:
            raise SimulationError("hashrate must be between 1 and 200000 attempts per simulated second")
        node = self.node(node_id)
        node.miner.hashrate = hashrate
        node.miner.refresh(force=True)

    @command
    def inject_invalid_block(self, node_id: str, kind: str) -> dict:
        """Make ``node_id`` behave maliciously: broadcast a deliberately invalid block."""
        if kind not in INVALID_BLOCK_KINDS:
            raise SimulationError(f"unknown invalid-block kind '{kind}' (choose from {', '.join(INVALID_BLOCK_KINDS)})")
        node = self.node(node_id)
        if node.status != "online":
            raise SimulationError(f"{node_id} is offline")
        bc, params = node.blockchain, self.params
        tip, idx, now = bc.latest, bc.height + 1, self.now_ms
        ts, bits = max(now, tip.timestamp_ms), params.difficulty_for(bc.height + 1)
        reward = params.block_reward * (20 if kind == "inflated_reward" else 1)
        txs = [make_coinbase(node.address, idx, ts, reward)]
        alice, bob = self.wallets.get("alice"), self.wallets.get("bob")
        if kind == "overspend" and alice and bob:
            txs.append(alice.create_transaction(bob.address, 10 ** 6, bc.tip_state.next_nonce(alice.address), ts))
        if kind == "forged_signature" and alice and bob:
            payload = Transaction(alice.address, bob.address, 5, bc.tip_state.next_nonce(alice.address), ts, alice.public_key)
            forged = dataclasses.replace(payload, signature=self._wallet("mallory").sign(payload.signing_payload()))
            txs.append(forged)
        block = Block(idx, ts, txs, tip.hash, bits)
        if kind == "bad_pow":
            nonce = 0
            while True:
                block.nonce = nonce
                block.seal()
                if not block.has_valid_pow():
                    break
                nonce += 1
        else:
            proof_of_work(block)
            if kind == "bad_hash":
                block.transactions[0] = dataclasses.replace(block.transactions[0], amount=reward + 1)
        self.events.emit(now, EventType.SCENARIO, f"{node_id} (acting maliciously) broadcasts an invalid block: {INVALID_BLOCK_KINDS[kind]}",
                         node_id, {"kind": kind})
        n = node._gossip(MessageType.BLOCK, {"block": block.to_dict()})
        return {"kind": kind, "hash": block.hash, "sent_to": n}

    @command
    def tamper_block(self, node_id: str, index: int) -> dict:
        """Corrupt a block in ONE node's local copy to demonstrate integrity checks."""
        node = self.node(node_id)
        chain = node.blockchain.chain
        if not isinstance(index, int) or not 1 <= index < len(chain):
            raise SimulationError(f"{node_id} has no block at height {index} that can be tampered with")
        block = chain[index]
        pos = 1 if len(block.transactions) > 1 else 0
        block.transactions[pos] = dataclasses.replace(block.transactions[pos], amount=block.transactions[pos].amount + 1)
        self.events.emit(self.now_ms, EventType.SCENARIO, f"{node_id}'s local copy of block #{index} was tampered with (no re-hash)", node_id)
        return {"index": index}

    def run_scenario(self, name: str, seed: Optional[int] = None) -> dict:
        from .scenarios import SCENARIOS, ScenarioRunner

        spec = SCENARIOS.get(name)
        if spec is None:
            raise SimulationError(f"unknown scenario '{name}' (choose from {', '.join(SCENARIOS)})")
        with self.lock:
            self._initialize(seed, spec["nodes"], spec["topology"], scenario=name)
            self.scenario = ScenarioRunner(self, name, spec)
            return {"name": name, "title": spec["title"]}

    def run_scenario_blocking(self, name: str, seed: Optional[int] = None, max_ms: int = 600_000) -> dict:
        """Run a scenario to completion without any wall-clock pacing (CLI / tests)."""
        info = self.run_scenario(name, seed)
        waited = 0
        while not self.scenario.done and waited < max_ms:
            self.advance(100)
            waited += 100
        if not self.scenario.done:
            raise SimulationError(f"scenario '{name}' did not finish within {max_ms / 1000:.0f} simulated seconds")
        self.advance(1000)  # let trailing messages settle
        info["problems"] = self.scenario.check()
        return info

    # ------------------------------------------------------------------
    # logs, replay, digest
    # ------------------------------------------------------------------
    def export_log(self) -> dict:
        with self.lock:
            return {"version": LOG_VERSION, "config": self.config, **self.header,
                    "commands": copy.deepcopy(self.command_log), "end_ms": self.now_ms,
                    "digest": self.state_digest()}

    def restore(self, log: dict) -> None:
        """Rebuild this simulation by replaying ``log`` from scratch."""
        if log.get("version") != LOG_VERSION:
            raise SimulationError("unsupported log version")
        with self.lock:
            self.config = merge_config(log.get("config"))
            self._replaying = True
            try:
                if log.get("scenario"):
                    self._initialize(log["seed"], log["initial_nodes"], log["topology"])
                    self.run_scenario(log["scenario"], log["seed"])
                else:
                    self._initialize(log["seed"], log["initial_nodes"], log["topology"])
                for cmd in log["commands"]:
                    if cmd["t"] > self.now_ms:
                        self.advance(cmd["t"] - self.now_ms)
                    getattr(self, cmd["op"])(*cmd["args"], **cmd["kwargs"])
                if log["end_ms"] > self.now_ms:
                    self.advance(log["end_ms"] - self.now_ms)
            finally:
                self._replaying = False
            self.command_log = copy.deepcopy(log["commands"])

    @classmethod
    def from_log(cls, log: dict) -> "Simulation":
        sim = cls(config=log.get("config"))
        sim.restore(log)
        return sim

    def state_digest(self) -> str:
        """Hash of the entire observable simulation state; equal digests = identical runs."""
        with self.lock:
            nodes = []
            for nid in sorted(self.network.nodes):
                n = self.network.nodes[nid]
                nodes.append({
                    "id": nid, "status": n.status, "peers": sorted(n.peers), "tip": n.blockchain.tip,
                    "blocks": sorted(n.blockchain.blocks), "mempool": sorted(t.id for t in n.mempool.all()),
                    "balances": sorted(n.blockchain.tip_state.balances.items()),
                    "mining": [n.miner.active, n.miner.total_attempts, n.miner.blocks_mined],
                })
            payload = {"now": self.now_ms, "nodes": nodes, "links": sorted(map(list, self.network.links)),
                       "msgs": self.network.stats, "difficulty": self.params.difficulty_schedule}
            return sha256_hex(json.dumps(payload, sort_keys=True))

    # ------------------------------------------------------------------
    # read models (everything the UI / CLI / API shows comes from here)
    # ------------------------------------------------------------------
    def _tx_view(self, tx: Transaction) -> dict:
        d = tx.to_dict()
        d["sender_name"] = self.name_of(tx.sender) if not tx.is_system else tx.sender
        d["receiver_name"] = self.name_of(tx.receiver)
        d.pop("public_key", None)  # keep payloads small; keys are public anyway
        return d

    def block_view(self, block: Block) -> dict:
        d = block.to_dict()
        d["miner"] = self.name_of(block.miner_address) if block.index else "genesis"
        d["transactions"] = [self._tx_view(t) for t in block.transactions]
        return d

    def snapshot(self) -> dict:
        with self.lock:
            net = self.network
            nodes = [n.info(net.live_peer_count(n.id)) for _, n in sorted(net.nodes.items(), key=lambda kv: (len(kv[0]), kv[0]))]
            top = max((n["height"] for n in nodes), default=0)
            tips = sorted({n["tip"] for n in nodes})
            for n in nodes:
                n["behind"] = top - n["height"]
                n["tip_short"] = n["tip"][:8]
                n["tip_group"] = tips.index(n["tip"])
            pending = {t.id for n in net.nodes.values() for t in n.mempool.all()}
            all_blocks = {h for n in net.nodes.values() for h in n.blockchain.blocks}
            fork = self.fork_view()
            links = [{"id": f"{a}|{b}", "source": a, "target": b, "latency_ms": net.latency_ms,
                      "state": "up" if net.nodes[a].status == "online" and net.nodes[b].status == "online" else "degraded"}
                     for a, b in sorted(net.links)]
            sc = self.scenario
            return {
                "sim": {"time_ms": self.now_ms, "running": self.running, "speed": self.speed, "seed": self.seed,
                        "digest": self.state_digest(), "generation": self.generation,
                        "difficulty": self.params.difficulty_for(top + 1), "latency_ms": net.latency_ms,
                        "jitter_ms": net.jitter_ms, "block_reward": self.params.block_reward,
                        "scenario": sc.status() if sc else None, "reset_event_id": self.reset_event_id},
                "overview": {
                    "total_nodes": len(nodes),
                    "connected_nodes": sum(1 for n in nodes if n["connection"] == "connected"),
                    "total_blocks": max(0, len(all_blocks) - 1),
                    "network_height": top,
                    "pending_txs": len(pending),
                    "active_miners": sum(1 for n in nodes if n["mining"] and n["status"] == "online"),
                    "total_messages": net.stats["sent"], "messages_delivered": net.stats["delivered"],
                    "messages_dropped": net.stats["dropped"], "in_flight": net.in_flight,
                    "competing_tips": len(tips), "fork_branches": fork["forks"],
                },
                "nodes": nodes, "links": links, "accounts": self.wallets_info(), "fork": fork,
            }

    def wallets_info(self) -> List[dict]:
        ref = self.canonical_node()
        state = ref.blockchain.tip_state if ref else None
        out = []
        for name in self.config["accounts"]:
            w = self.wallets[name]
            out.append({"name": name, "address": w.address,
                        "balance": state.balance(w.address) if state else 0,
                        "next_nonce": state.next_nonce(w.address) if state else 0})
        return out

    def fork_view(self, window: int = 30) -> dict:
        union: Dict[str, dict] = {}
        for nid in sorted(self.network.nodes):
            bc = self.network.nodes[nid].blockchain
            best = bc.best_hashes()
            for h, b in bc.blocks.items():
                e = union.setdefault(h, {"hash": h, "index": b.index, "prev": b.previous_hash,
                                         "miner": self.name_of(b.miner_address) if b.index else "genesis",
                                         "txs": max(0, len(b.transactions) - 1), "best_on": [], "known_by": []})
                e["known_by"].append(nid)
                if h in best:
                    e["best_on"].append(nid)
        parents = {e["prev"] for e in union.values()}
        leaves = [h for h in union if h not in parents]
        top = max((e["index"] for e in union.values()), default=0)
        lo = max(0, top - window)
        blocks = sorted((e for e in union.values() if e["index"] >= lo), key=lambda e: (e["index"], e["hash"]))
        return {"blocks": blocks, "forks": max(0, len(leaves) - 1), "min_index": lo,
                "tips": sorted(union[h]["hash"] for h in leaves)}

    def node_chain(self, node_id: str) -> dict:
        node = self.node(node_id)
        bc = node.blockchain
        return {"node": node_id, "height": bc.height, "tip": bc.tip,
                "blocks": [self.block_view(b) for b in bc.chain],
                "side_blocks": [self.block_view(b) for b in sorted(bc.blocks.values(), key=lambda b: bc.arrival[b.hash])
                                if b.hash not in bc.best_hashes()]}

    def node_detail(self, node_id: str) -> dict:
        node = self.node(node_id)
        d = node.info(self.network.live_peer_count(node_id))
        d["mempool_txs"] = [self._tx_view(t) for t in node.mempool.all()]
        d["recent_messages"] = list(node.recent_messages)
        d["tip_short"] = d["tip"][:8]
        d["miner_name"] = self.name_of(node.address)
        return d

    def tx_explorer(self) -> dict:
        with self.lock:
            net = self.network
            total = len(net.nodes)
            pending = []
            seen_pending = set()
            for nid in sorted(net.nodes):
                for tx in net.nodes[nid].mempool.all():
                    if tx.id in seen_pending:
                        continue
                    seen_pending.add(tx.id)
                    rec = net.tx_tracker.get(tx.id, {})
                    holders = sorted(n for n, node in net.nodes.items() if tx.id in node.mempool)
                    pending.append({**self._tx_view(tx), "origin": rec.get("origin"), "created_ms": rec.get("created_ms"),
                                    "held_by": holders, "propagation": f"{len(holders)}/{total}"})
            confirmed = []
            ref = self.canonical_node()
            if ref:
                chain = ref.blockchain.chain
                for b in chain[1:]:
                    agree = sum(1 for n in net.nodes.values() if b.hash in n.blockchain.blocks and b.hash in n.blockchain.best_hashes())
                    for t in b.transactions[1:]:
                        confirmed.append({**self._tx_view(t), "block": b.index, "block_hash": b.hash,
                                          "confirmations": ref.blockchain.height - b.index + 1,
                                          "agreement": f"{agree}/{total}"})
            rejected = []
            for nid in sorted(net.nodes):
                for r in net.nodes[nid].mempool.rejected:
                    tx = Transaction.from_dict(r["tx"])
                    rejected.append({**self._tx_view(tx), "node": nid, "reason": r["reason"], "time_ms": r["time_ms"]})
            rejected.sort(key=lambda r: (r["time_ms"], r["node"], r["id"]))
            return {"pending": pending, "confirmed": confirmed[-100:], "rejected": rejected[-100:], "nodes": total}

    def validate_all(self) -> dict:
        with self.lock:
            return {nid: self.network.nodes[nid].blockchain.verify_integrity() for nid in sorted(self.network.nodes)}

    def consensus_info(self) -> dict:
        return {"assumptions": CONSENSUS_ASSUMPTIONS, "block_reward": self.params.block_reward,
                "max_txs_per_block": self.params.max_txs_per_block,
                "difficulty_schedule": self.params.difficulty_schedule}
