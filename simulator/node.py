"""A blockchain node: independent chain, mempool, peers, miner and message handlers."""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Optional, Set

from . import consensus
from .block import Block
from .blockchain import Blockchain, Outcome
from .consensus import ConsensusParams
from .events import EventType
from .mempool import Mempool
from .message import Message, MessageType
from .miner import Miner
from .transaction import Transaction

CHAIN_REQUEST_COOLDOWN_MS = 1000


class Node:
    def __init__(self, node_id: str, address: str, ctx, params: ConsensusParams, genesis: Block,
                 hashrate: int, tick_ms: int):
        self.id = node_id
        self.address = address  # where this node's mining rewards go
        self.ctx = ctx  # the Network: clock, event log and message transport
        self.params = params
        # Each node owns a private copy of the genesis block -> truly independent state.
        self.blockchain = Blockchain(Block.from_dict(genesis.to_dict()), params)
        self.mempool = Mempool()
        self.peers: Set[str] = set()
        self.status = "online"  # online | offline
        self.removed = False
        self.miner = Miner(self, hashrate, tick_ms)
        self.seen_messages: Set[str] = set()
        self.seen_objects: Set[str] = set()  # tx ids and block hashes already handled
        self.queue: Deque[Message] = deque()
        self.recent_messages: Deque[dict] = deque(maxlen=25)
        self._processing = False
        self._last_chain_request: Dict[str, int] = {}
        self.stats = {"sent": 0, "received": 0, "duplicates": 0, "blocks_rejected": 0,
                      "txs_rejected": 0, "reorgs": 0, "forks_seen": 0, "last_activity_ms": 0}

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _emit(self, type_: str, message: str, data: Optional[dict] = None) -> None:
        self.ctx.emit(type_, message, self.id, data)

    def _send(self, peer: str, mtype: MessageType, payload: dict, bypass: bool = False) -> None:
        self.stats["sent"] += 1
        self.ctx.send(self.id, peer, mtype, payload, bypass)

    def _gossip(self, mtype: MessageType, payload: dict, exclude: Optional[str] = None) -> int:
        n = 0
        for peer in sorted(self.peers):
            if peer != exclude:
                self._send(peer, mtype, payload)
                n += 1
        return n

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------
    def go_offline(self) -> None:
        self.status = "offline"
        self.miner.suspend()
        self._emit(EventType.NODE_STATUS, f"{self.id} went offline")

    def go_online(self) -> None:
        self.status = "online"
        self._emit(EventType.NODE_STATUS, f"{self.id} is back online and re-announces itself")
        for peer in sorted(self.peers):
            self._send(peer, MessageType.PEER_CONNECT, self._hello(reply=True))
        self.miner.refresh(force=True)

    def _hello(self, reply: bool) -> dict:
        return {"reply": reply, "height": self.blockchain.height, "tip": self.blockchain.tip}

    # ------------------------------------------------------------------
    # transactions
    # ------------------------------------------------------------------
    def _admit_tx(self, tx: Transaction):
        ok, reason = self.mempool.add(tx, self.blockchain.tip_state, self.ctx.now_ms)
        self.ctx.track_tx(self.id, tx, ok, reason)
        if ok:
            self.miner.refresh()  # fold the new transaction into the block template
        else:
            self.stats["txs_rejected"] += 1
            self._emit(EventType.TX_REJECTED, f"{self.id} rejected tx {tx.id[:8]}: {reason}",
                       {"tx_id": tx.id, "reason": reason})
        return ok, reason

    def submit_transaction(self, tx: Transaction):
        """Entry point for a transaction created at (submitted to) this node."""
        if self.status != "online":
            return False, f"{self.id} is offline"
        self.seen_objects.add(tx.id)
        ok, reason = self._admit_tx(tx)
        if ok:
            self._emit(EventType.TX_CREATED, f"tx {tx.id[:8]} created at {self.id} ({tx.amount} coins)", {"tx_id": tx.id})
            n = self._gossip(MessageType.TRANSACTION, {"tx": tx.to_dict()})
            self._emit(EventType.TX_BROADCAST, f"{self.id} broadcast tx {tx.id[:8]} to {n} peer(s)", {"tx_id": tx.id})
        return ok, reason

    # ------------------------------------------------------------------
    # mining
    # ------------------------------------------------------------------
    def on_block_mined(self, block: Block, duration_ms: int, attempts: int) -> None:
        old_tip = self.blockchain.tip
        result = self.blockchain.add_block(block)
        self.seen_objects.add(block.hash)
        if result.outcome == Outcome.INVALID:  # should never happen; surface it loudly
            self._emit(EventType.ERROR, f"{self.id} mined an invalid block: {result.reason}")
            return
        self._emit(EventType.BLOCK_MINED,
                   f"{self.id} mined block #{block.index} {block.hash[:8]} after {attempts} attempts ({duration_ms / 1000:.2f}s sim)",
                   {"index": block.index, "hash": block.hash, "attempts": attempts,
                    "duration_ms": duration_ms, "nonce": block.nonce, "txs": len(block.transactions) - 1})
        if self.blockchain.tip != old_tip:
            self._on_tip_changed(old_tip)
        n = self._gossip(MessageType.BLOCK, {"block": block.to_dict()})
        self._emit(EventType.BLOCK_BROADCAST, f"{self.id} broadcast block #{block.index} to {n} peer(s)")

    def _on_tip_changed(self, old_tip: str) -> None:
        bc, now = self.blockchain, self.ctx.now_ms
        fork, orphaned, adopted = consensus.reorg_path(bc.blocks, old_tip, bc.tip)
        state = bc.tip_state
        if orphaned:
            self.stats["reorgs"] += 1
            adopted_ids = {t.id for b in adopted for t in b.transactions}
            recovered, lost = 0, []
            for b in orphaned:
                for tx in b.transactions:
                    if tx.is_system or tx.id in adopted_ids:
                        continue
                    ok, reason = self.mempool.add(tx, state, now)
                    self.ctx.track_tx(self.id, tx, ok, reason)
                    if ok:
                        recovered += 1
                    else:
                        lost.append((tx, reason))
            self._emit(EventType.CHAIN_REORG,
                       f"{self.id} reorganised: dropped {len(orphaned)} block(s) after height {fork.index}, "
                       f"adopted {len(adopted)}; {recovered} tx recovered, {len(lost)} rejected",
                       {"depth": len(orphaned), "fork_height": fork.index, "adopted": len(adopted),
                        "recovered": recovered, "rejected": len(lost),
                        "new_tip": bc.tip, "orphaned": [b.hash for b in orphaned]})
            for tx, reason in lost:
                self.stats["txs_rejected"] += 1
                self._emit(EventType.TX_REJECTED, f"{self.id} rejected orphaned tx {tx.id[:8]}: {reason}",
                           {"tx_id": tx.id, "reason": reason})
        self.mempool.remove_confirmed([t for b in adopted for t in b.transactions if not t.is_system])
        for tx, reason in self.mempool.revalidate(state, now):
            self.stats["txs_rejected"] += 1
            self._emit(EventType.TX_REJECTED, f"{self.id} evicted tx {tx.id[:8]}: {reason}",
                       {"tx_id": tx.id, "reason": reason})
        self.miner.refresh(force=True)

    # ------------------------------------------------------------------
    # message handling
    # ------------------------------------------------------------------
    def enqueue(self, msg: Message) -> None:
        """Message queue: messages are handled one at a time, in arrival order."""
        self.queue.append(msg)
        if self._processing:
            return
        self._processing = True
        try:
            while self.queue:
                self._handle(self.queue.popleft())
        finally:
            self._processing = False

    def _handle(self, msg: Message) -> None:
        if self.removed:
            return
        self.stats["last_activity_ms"] = self.ctx.now_ms
        if msg.id in self.seen_messages:
            self.stats["duplicates"] += 1
            return
        self.seen_messages.add(msg.id)
        self.stats["received"] += 1
        self.recent_messages.append(msg.summary())
        handler = {
            MessageType.TRANSACTION: self._on_transaction,
            MessageType.BLOCK: self._on_block,
            MessageType.CHAIN_REQUEST: self._on_chain_request,
            MessageType.CHAIN_RESPONSE: self._on_chain_response,
            MessageType.PEER_CONNECT: self._on_peer_connect,
            MessageType.PEER_DISCONNECT: self._on_peer_disconnect,
        }[msg.type]
        try:
            handler(msg)
        except (KeyError, TypeError, ValueError) as exc:  # malformed payload from a peer
            self._emit(EventType.ERROR, f"{self.id} ignored malformed {msg.type.value} from {msg.sender}: {exc}")

    def _on_transaction(self, msg: Message) -> None:
        tx = Transaction.from_dict(msg.payload["tx"])
        if tx.id in self.seen_objects:
            self.stats["duplicates"] += 1
            return
        self.seen_objects.add(tx.id)
        ok, _ = self._admit_tx(tx)
        if ok:
            self._emit(EventType.TX_RECEIVED, f"{self.id} received tx {tx.id[:8]} from {msg.sender}",
                       {"tx_id": tx.id, "from": msg.sender})
            self._gossip(MessageType.TRANSACTION, msg.payload, exclude=msg.sender)

    def _on_block(self, msg: Message) -> None:
        block = Block.from_dict(msg.payload["block"])
        if block.hash in self.seen_objects:
            self.stats["duplicates"] += 1
            return
        self.seen_objects.add(block.hash)
        old_tip = self.blockchain.tip
        outcome = self._ingest(block, msg.sender, verbose=True)
        if outcome in (Outcome.ADDED, Outcome.SIDE_BRANCH, Outcome.REORG):
            self._gossip(MessageType.BLOCK, msg.payload, exclude=msg.sender)
        if self.blockchain.tip != old_tip:
            self._on_tip_changed(old_tip)

    def _ingest(self, block: Block, source: str, verbose: bool) -> Outcome:
        bc = self.blockchain
        before_tip = bc.latest
        result = bc.add_block(block)
        tag = f"block #{block.index} {block.hash[:8]}"
        if result.outcome == Outcome.INVALID:
            self.stats["blocks_rejected"] += 1
            self._emit(EventType.BLOCK_REJECTED, f"{self.id} rejected {tag} from {source}: {result.reason}",
                       {"hash": block.hash, "index": block.index, "reason": result.reason, "from": source})
        elif result.outcome == Outcome.ORPHAN:
            if verbose:
                self._emit(EventType.BLOCK_RECEIVED, f"{self.id} got {tag} with unknown parent; requesting {source}'s chain",
                           {"hash": block.hash, "index": block.index, "from": source})
            self._request_chain(source)
        elif result.outcome in (Outcome.ADDED, Outcome.SIDE_BRANCH, Outcome.REORG):
            self.seen_objects.add(block.hash)
            if verbose:
                self._emit(EventType.BLOCK_RECEIVED, f"{self.id} accepted {tag} from {source}",
                           {"hash": block.hash, "index": block.index, "from": source})
            if result.outcome == Outcome.SIDE_BRANCH:
                self.stats["forks_seen"] += 1
                self._emit(EventType.FORK_DETECTED,
                           f"{self.id} detected a fork at height {block.index}: {tag} competes with tip {before_tip.hash[:8]}",
                           {"height": block.index, "competing": [block.hash, before_tip.hash]})
        return result.outcome

    def _request_chain(self, peer: str) -> None:
        last = self._last_chain_request.get(peer)
        if last is not None and self.ctx.now_ms - last < CHAIN_REQUEST_COOLDOWN_MS:
            return
        self._last_chain_request[peer] = self.ctx.now_ms
        self._send(peer, MessageType.CHAIN_REQUEST, {"height": self.blockchain.height, "tip": self.blockchain.tip}, bypass=False)

    def _on_chain_request(self, msg: Message) -> None:
        chain = self.blockchain.chain
        self._send(msg.sender, MessageType.CHAIN_RESPONSE,
                   {"blocks": [b.to_dict() for b in chain[1:]], "height": self.blockchain.height})

    def _on_chain_response(self, msg: Message) -> None:
        blocks = [Block.from_dict(b) for b in msg.payload["blocks"]]
        old_tip = self.blockchain.tip
        new, rejected = 0, 0
        for block in blocks:
            if block.hash in self.blockchain.blocks:
                continue
            outcome = self._ingest(block, msg.sender, verbose=False)
            if outcome == Outcome.INVALID:
                rejected += 1
                break  # the rest of an invalid chain cannot connect
            if outcome in (Outcome.ADDED, Outcome.SIDE_BRANCH, Outcome.REORG):
                new += 1
        self._emit(EventType.CHAIN_SYNC,
                   f"{self.id} synchronised with {msg.sender}: {new} new block(s), height {self.blockchain.height}",
                   {"new_blocks": new, "from": msg.sender, "height": self.blockchain.height})
        if self.blockchain.tip != old_tip:
            self._on_tip_changed(old_tip)
            tip = self.blockchain.latest
            self._gossip(MessageType.BLOCK, {"block": tip.to_dict()}, exclude=msg.sender)

    def _on_peer_connect(self, msg: Message) -> None:
        peer = msg.sender
        newly = peer not in self.peers
        self.peers.add(peer)
        if newly:
            self._emit(EventType.PEER_CONNECTED, f"{self.id} is now peered with {peer}", {"peer": peer})
        if msg.payload.get("reply"):
            self._send(peer, MessageType.PEER_CONNECT, self._hello(reply=False))
        if msg.payload.get("tip") not in self.blockchain.blocks:
            self._request_chain(peer)

    def _on_peer_disconnect(self, msg: Message) -> None:
        # ignore a stale goodbye if the two nodes have already reconnected
        key = (msg.sender, self.id) if msg.sender < self.id else (self.id, msg.sender)
        if key not in self.ctx.links:
            self.peers.discard(msg.sender)

    def on_link_lost(self, peer: str) -> None:
        if peer in self.peers:
            self.peers.discard(peer)
            self._emit(EventType.PEER_DISCONNECTED, f"{self.id} lost its connection to {peer}", {"peer": peer})

    # ------------------------------------------------------------------
    def connection_state(self, live_peers: int) -> str:
        if self.status != "online":
            return "offline"
        return "connected" if live_peers else "isolated"

    def info(self, live_peers: int) -> dict:
        bc = self.blockchain
        return {
            "id": self.id, "address": self.address, "status": self.status,
            "connection": self.connection_state(live_peers),
            "peers": sorted(self.peers), "height": bc.height, "tip": bc.tip,
            "mempool": len(self.mempool), "mining": self.miner.active,
            "blocks_known": len(bc.blocks), "side_branches": len(bc.side_tips()),
            "miner": self.miner.info(), "stats": dict(self.stats),
        }
