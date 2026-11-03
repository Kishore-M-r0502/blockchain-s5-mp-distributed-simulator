"""Predefined, reproducible scenarios.

A scenario is a list of steps ``(action, arguments, wait)``. The runner executes a
step, then waits until ``wait`` is satisfied before moving on:

* ``"quiet"``  - no message is in flight any more (everything has propagated)
* ``"idle"``   - no node is still mining and nothing is in flight
* an ``int``   - that many simulated milliseconds
* ``None``     - continue immediately

Because the simulation is deterministic, the same scenario with the same seed always
produces the same blocks, forks and reorganisations.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from .events import EventType

POLL_MS = 50

A = ["node-1", "node-2", "node-3"]
B = ["node-4", "node-5", "node-6"]


def _tips(sim, ids):
    return {sim.network.nodes[i].blockchain.tip for i in ids}


def _heights(sim, ids):
    return {sim.network.nodes[i].blockchain.height for i in ids}


def _balance(sim, name, node="node-1"):
    return sim.network.nodes[node].blockchain.tip_state.balance(sim.wallets[name].address)


def _converged(sim) -> List[str]:
    ids = sorted(sim.network.nodes)
    problems = []
    if len(_tips(sim, ids)) != 1:
        problems.append("nodes do not agree on one chain tip")
    if any(len(sim.network.nodes[i].mempool) for i in ids):
        problems.append("some mempools are not empty")
    return problems


def check_normal(sim) -> List[str]:
    problems = _converged(sim)
    if _heights(sim, sorted(sim.network.nodes)) != {2}:
        problems.append("expected every node at height 2")
    if (_balance(sim, "bob"), _balance(sim, "alice")) != (1025, 985):
        problems.append("balances do not match the confirmed payments")
    return problems


def check_partition(sim) -> List[str]:
    problems = []
    if len(_tips(sim, A)) != 1 or len(_tips(sim, B)) != 1:
        problems.append("each group should agree internally")
    if _tips(sim, A) == _tips(sim, B):
        problems.append("the two groups should hold different chains")
    if _heights(sim, A + B) != {1}:
        problems.append("expected both groups at height 1")
    if any(set(sim.network.nodes[a].peers) & set(B) for a in A):
        problems.append("a node still peers across the partition")
    return problems


def check_fork(sim) -> List[str]:
    problems = []
    if _heights(sim, A) != {2} or _heights(sim, B) != {3}:
        problems.append("expected group A at height 2 and group B at height 3")
    if _tips(sim, A) == _tips(sim, B):
        problems.append("groups should hold competing chains")
    if sim.fork_view()["forks"] < 1:
        problems.append("the fork view should show a competing branch")
    return problems


def check_recovery(sim) -> List[str]:
    problems = _converged(sim)
    if _heights(sim, sorted(sim.network.nodes)) != {4}:
        problems.append("expected every node at height 4 after recovery")
    if not any(sim.network.nodes[a].stats["reorgs"] for a in A):
        problems.append("group A should have reorganised onto the longer chain")
    if (_balance(sim, "bob"), _balance(sim, "erin")) != (1025, 1040):
        problems.append("the orphaned payment should have been recovered and re-confirmed")
    return problems


def check_invalid(sim) -> List[str]:
    problems = _converged(sim)
    if _heights(sim, sorted(sim.network.nodes)) != {1}:
        problems.append("no invalid block may be accepted: expected height 1")
    rejected = [e for e in sim.events.since(0) if e.type == EventType.BLOCK_REJECTED]
    if len(rejected) < len(("bad_pow", "bad_hash", "inflated_reward", "overspend", "forged_signature")):
        problems.append("every invalid block should be rejected by the attacker's peers")
    return problems


def check_double_spend(sim) -> List[str]:
    problems = _converged(sim)
    if _heights(sim, sorted(sim.network.nodes)) != {2}:
        problems.append("expected height 2 after the partition heals")
    if (_balance(sim, "bob"), _balance(sim, "carol"), _balance(sim, "alice")) != (1000, 1900, 100):
        problems.append("only one of the conflicting payments may be confirmed")
    reasons = [r["reason"] for n in sim.network.nodes.values() for r in n.mempool.rejected]
    if not any("double spend" in r for r in reasons):
        problems.append("a double-spend rejection should have been logged")
    return problems


SCENARIOS: Dict[str, dict] = {
    "normal": {
        "title": "Normal network",
        "summary": "Six well-connected nodes gossip transactions; two different miners confirm them and everyone converges.",
        "nodes": 6, "topology": "ring+chord", "check": check_normal,
        "steps": [
            ("note", {"text": "Six nodes in a ring with one chord. Alice pays Bob at node-1 and Carol pays Dave at node-4."}, None),
            ("tx", {"sender": "alice", "receiver": "bob", "amount": 25, "node": "node-1"}, None),
            ("tx", {"sender": "carol", "receiver": "dave", "amount": 40, "node": "node-4"}, "quiet"),
            ("note", {"text": "Both transactions reached every mempool. node-2 now mines one block."}, None),
            ("mine", {"nodes": {"node-2": 1}}, "idle"),
            ("note", {"text": "Block 1 has propagated. Erin pays Alice at node-5; node-3 mines the next block."}, None),
            ("tx", {"sender": "erin", "receiver": "alice", "amount": 10, "node": "node-5"}, "quiet"),
            ("mine", {"nodes": {"node-3": 1}}, "idle"),
            ("note", {"text": "All nodes share height 2 and the same tip."}, None),
        ],
    },
    "partition": {
        "title": "Network partition",
        "summary": "The network splits into {1,2,3} and {4,5,6}. Each side keeps working but cannot see the other.",
        "nodes": 6, "topology": "ring+chord", "check": check_partition,
        "steps": [
            ("note", {"text": "Cutting every link between {node-1..3} and {node-4..6}."}, None),
            ("partition", {"groups": [A, B]}, "quiet"),
            ("tx", {"sender": "alice", "receiver": "bob", "amount": 25, "node": "node-1"}, None),
            ("tx", {"sender": "dave", "receiver": "erin", "amount": 40, "node": "node-5"}, "quiet"),
            ("note", {"text": "Each group only knows its own transaction. node-2 and node-5 each mine one block."}, None),
            ("mine", {"nodes": {"node-2": 1}}, "idle"),
            ("mine", {"nodes": {"node-5": 1}}, "idle"),
            ("note", {"text": "Both groups are at height 1 but hold different blocks: the network disagrees."}, None),
        ],
    },
    "fork": {
        "title": "Blockchain fork",
        "summary": "Two partitions mine competing chains of different length (2 vs 3 blocks).",
        "nodes": 6, "topology": "ring+chord", "check": check_fork,
        "steps": [
            ("partition", {"groups": [A, B]}, "quiet"),
            ("tx", {"sender": "alice", "receiver": "bob", "amount": 25, "node": "node-1"}, None),
            ("tx", {"sender": "dave", "receiver": "erin", "amount": 40, "node": "node-5"}, "quiet"),
            ("note", {"text": "Group A (node-2) mines 2 blocks while group B (node-5) mines 3 blocks, at the same time."}, None),
            ("mine", {"nodes": {"node-2": 2, "node-5": 3}}, "idle"),
            ("note", {"text": "Two competing chains exist. Open the Forks tab to see both branches growing from the same parent."}, None),
        ],
    },
    "recovery": {
        "title": "Network recovery",
        "summary": "After a fork, the partitions reconnect; the shorter chain is abandoned and its transactions are recovered.",
        "nodes": 6, "topology": "ring+chord", "check": check_recovery,
        "steps": [
            ("partition", {"groups": [A, B]}, "quiet"),
            ("tx", {"sender": "alice", "receiver": "bob", "amount": 25, "node": "node-1"}, None),
            ("tx", {"sender": "dave", "receiver": "erin", "amount": 40, "node": "node-5"}, "quiet"),
            ("mine", {"nodes": {"node-2": 2, "node-5": 3}}, "idle"),
            ("note", {"text": "Fork in place: A has 2 blocks, B has 3. Reconnecting the groups now."}, None),
            ("heal", {}, "quiet"),
            ("note", {"text": "Group A adopted the longer chain. Alice's payment sat in an orphaned block and is back in the mempools."}, None),
            ("mine", {"nodes": {"node-1": 1}}, "idle"),
            ("note", {"text": "node-1 mined the recovered payment into block 4. Every node agrees again."}, None),
        ],
    },
    "invalid": {
        "title": "Invalid block",
        "summary": "A misbehaving node broadcasts five kinds of invalid blocks; its peers reject every one.",
        "nodes": 6, "topology": "ring+chord", "check": check_invalid,
        "steps": [
            ("mine", {"nodes": {"node-1": 1}}, "idle"),
            ("note", {"text": "A healthy chain of height 1. node-3 now turns malicious."}, None),
            ("invalid", {"node": "node-3", "kind": "bad_pow"}, "quiet"),
            ("invalid", {"node": "node-3", "kind": "bad_hash"}, "quiet"),
            ("invalid", {"node": "node-3", "kind": "inflated_reward"}, "quiet"),
            ("invalid", {"node": "node-3", "kind": "overspend"}, "quiet"),
            ("invalid", {"node": "node-3", "kind": "forged_signature"}, "quiet"),
            ("note", {"text": "All five blocks were rejected by node-3's peers and never relayed. The chain stayed at height 1."}, None),
        ],
    },
    "double_spend": {
        "title": "Double spending",
        "summary": "Alice signs two payments with the same nonce on opposite sides of a partition; only one survives.",
        "nodes": 6, "topology": "ring+chord", "check": check_double_spend,
        "steps": [
            ("partition", {"groups": [A, B]}, "quiet"),
            ("note", {"text": "Alice (balance 1000) pays Bob 900 via node-1 and, with the SAME nonce, pays Carol 900 via node-4."}, None),
            ("tx", {"sender": "alice", "receiver": "bob", "amount": 900, "node": "node-1", "nonce": 0}, None),
            ("tx", {"sender": "alice", "receiver": "carol", "amount": 900, "node": "node-4", "nonce": 0}, "quiet"),
            ("note", {"text": "Each side accepted one payment. A third attempt at node-2 is refused immediately: its mempool already holds nonce 0."}, None),
            ("tx", {"sender": "alice", "receiver": "erin", "amount": 900, "node": "node-2", "nonce": 0}, "quiet"),
            ("mine", {"nodes": {"node-2": 1, "node-5": 2}}, "idle"),
            ("note", {"text": "Each side confirmed a different spend. Reconnecting: the longer chain (Carol's payment) wins."}, None),
            ("heal", {}, "quiet"),
            ("note", {"text": "Alice's payment to Bob sat in an orphaned block. It cannot return: nonce 0 is already used on the winning chain."}, None),
        ],
    },
}


def scenario_list() -> List[dict]:
    return [{"name": k, "title": v["title"], "summary": v["summary"], "nodes": v["nodes"], "steps": len(v["steps"])}
            for k, v in SCENARIOS.items()]


class ScenarioRunner:
    def __init__(self, sim, name: str, spec: dict):
        self.sim, self.name, self.spec = sim, name, spec
        self.steps = spec["steps"]
        self.index = 0
        self.done = False
        self._wait: Optional[object] = None
        self._resume_at = 0
        sim.events.emit(sim.now_ms, EventType.SCENARIO, f"scenario '{spec['title']}': {spec['summary']}")
        sim.scheduler.schedule(0, self._poll)

    def status(self) -> dict:
        return {"name": self.name, "title": self.spec["title"], "summary": self.spec["summary"],
                "step": self.index, "total": len(self.steps), "done": self.done}

    def check(self) -> List[str]:
        return list(self.spec["check"](self.sim))

    def _ready(self) -> bool:
        w, net = self._wait, self.sim.network
        if w is None:
            return True
        if isinstance(w, int):
            return self.sim.now_ms >= self._resume_at
        if w == "quiet":
            return net.in_flight == 0
        return net.in_flight == 0 and not any(n.miner.active for n in net.nodes.values())  # idle

    def _poll(self) -> None:
        if self.done:
            return
        if not self._ready():
            self.sim.scheduler.schedule(POLL_MS, self._poll)
            return
        if self.index >= len(self.steps):
            self.done = True
            self.sim.events.emit(self.sim.now_ms, EventType.SCENARIO, f"scenario '{self.spec['title']}' finished")
            return
        action, kwargs, wait = self.steps[self.index]
        self.index += 1
        try:
            self._execute(action, kwargs)
        except Exception as exc:  # a failed step must not wedge the scenario
            self.sim.events.emit(self.sim.now_ms, EventType.ERROR, f"scenario step '{action}' failed: {exc}")
        self._wait = wait
        self._resume_at = self.sim.now_ms + wait if isinstance(wait, int) else 0
        self.sim.scheduler.schedule(POLL_MS if wait is not None else 0, self._poll)

    def _execute(self, action: str, kw: dict) -> None:
        sim = self.sim
        if action == "note":
            sim.events.emit(sim.now_ms, EventType.SCENARIO, kw["text"])
        elif action == "partition":
            sim.partition(kw["groups"])
        elif action == "heal":
            sim.heal()
        elif action == "tx":
            sim.create_transaction(kw["sender"], kw["receiver"], kw["amount"], kw["node"], kw.get("nonce"))
        elif action == "mine":
            for node_id, blocks in kw["nodes"].items():
                sim.start_mining(node_id, blocks)
        elif action == "invalid":
            sim.inject_invalid_block(kw["node"], kw["kind"])
        else:
            raise ValueError(f"unknown scenario action {action}")
