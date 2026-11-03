"""Simulated peer-to-peer network: nodes, links, latency and message delivery.

Nodes never touch each other's state. They call ``Network.send`` and the message
is delivered later by the scheduler (after a seeded, configurable latency),
unless the sender or receiver is offline or the link no longer exists.

Connection model: peer connections are *undirected* (a TCP-like handshake), so a
link is drawn as a plain line. Messages themselves are directed (animated particles).
"""
from __future__ import annotations

import random
from collections import deque
from typing import Callable, Deque, Dict, List, Optional, Tuple

from .block import Block
from .consensus import ConsensusParams
from .events import EventLog, EventType
from .message import Message, MessageType
from .node import Node
from .scheduler import Scheduler
from .transaction import Transaction


def link_key(a: str, b: str) -> Tuple[str, str]:
    return (a, b) if a < b else (b, a)


class Network:
    def __init__(self, scheduler: Scheduler, events: EventLog, params: ConsensusParams, genesis: Block,
                 rng: random.Random, wallet_address: Callable[[str], str], latency_ms: int = 400,
                 jitter_ms: int = 100, hashrate: int = 1000, tick_ms: int = 100):
        self.scheduler = scheduler
        self.events = events
        self.params = params
        self.genesis = genesis
        self.rng = rng
        self.wallet_address = wallet_address  # creates/returns the miner address for a node id
        self.latency_ms = latency_ms
        self.jitter_ms = jitter_ms
        self.default_hashrate = hashrate
        self.tick_ms = tick_ms
        self.nodes: Dict[str, Node] = {}
        self.links: Dict[Tuple[str, str], dict] = {}
        self.severed: List[Tuple[str, str]] = []  # links cut by partition(), restored by heal()
        self._node_counter = 0
        self._msg_counter = 0
        self.in_flight = 0
        self.stats = {"sent": 0, "delivered": 0, "dropped": 0}
        self.feed: List[dict] = []  # new messages since the UI last drained it
        self.recent: Deque[dict] = deque(maxlen=60)
        self.tx_tracker: Dict[str, dict] = {}

    # ---- services used by nodes -------------------------------------
    @property
    def now_ms(self) -> int:
        return self.scheduler.now_ms

    def emit(self, type_: str, message: str, node: Optional[str] = None, data: Optional[dict] = None):
        return self.events.emit(self.now_ms, type_, message, node, data)

    def schedule(self, delay_ms: int, fn: Callable[[], None]) -> None:
        self.scheduler.schedule(delay_ms, fn)

    def track_tx(self, node_id: str, tx: Transaction, accepted: bool, reason: str) -> None:
        rec = self.tx_tracker.setdefault(tx.id, {"tx": tx.to_dict(), "created_ms": self.now_ms,
                                                  "origin": node_id, "seen": {}, "rejected": {}})
        if accepted:
            rec["seen"][node_id] = self.now_ms
            rec["rejected"].pop(node_id, None)
        else:
            rec["rejected"][node_id] = {"reason": reason, "time_ms": self.now_ms}

    def sample_latency(self) -> int:
        jitter = self.rng.randint(-self.jitter_ms, self.jitter_ms) if self.jitter_ms else 0
        return max(1, self.latency_ms + jitter)

    def send(self, sender: str, receiver: str, mtype: MessageType, payload: dict, bypass: bool = False) -> Message:
        self._msg_counter += 1
        msg = Message(f"msg-{self._msg_counter:06d}", sender, receiver, mtype, self.now_ms, payload)
        self.stats["sent"] += 1
        s, r = self.nodes.get(sender), self.nodes.get(receiver)
        reason = ""
        if s is None or r is None:
            reason = "unknown node"
        elif s.status != "online":
            reason = f"sender {sender} is offline"
        elif not bypass and link_key(sender, receiver) not in self.links:
            reason = "no connection between nodes"
        if reason:
            self._drop(msg, reason)
            return msg
        msg.deliver_at_ms = self.now_ms + self.sample_latency()
        self.in_flight += 1
        self.feed.append(msg.summary())
        self.scheduler.schedule_at(msg.deliver_at_ms, lambda: self._deliver(msg, bypass))
        return msg

    def _drop(self, msg: Message, reason: str) -> None:
        msg.status, msg.drop_reason = "dropped", reason
        self.stats["dropped"] += 1
        self.recent.append(msg.summary())
        self.emit(EventType.MESSAGE_DROPPED,
                  f"{msg.type.value} {msg.sender} -> {msg.receiver} dropped: {reason}",
                  msg.sender, {"msg_id": msg.id, "to": msg.receiver, "reason": reason, "type": msg.type.value})

    def _deliver(self, msg: Message, bypass: bool) -> None:
        self.in_flight -= 1
        r = self.nodes.get(msg.receiver)
        if r is None or r.removed:
            return self._drop(msg, "receiver no longer exists")
        if r.status != "online":
            return self._drop(msg, f"receiver {r.id} is offline")
        if not bypass and link_key(msg.sender, msg.receiver) not in self.links:
            return self._drop(msg, "connection closed while message was in transit")
        msg.status = "delivered"
        self.stats["delivered"] += 1
        self.recent.append(msg.summary())
        r.enqueue(msg)

    # ---- topology ----------------------------------------------------
    def add_node(self, node_id: Optional[str] = None, hashrate: Optional[int] = None) -> Node:
        if node_id is None:
            self._node_counter += 1
            node_id = f"node-{self._node_counter}"
            while node_id in self.nodes:
                self._node_counter += 1
                node_id = f"node-{self._node_counter}"
        elif node_id in self.nodes:
            raise ValueError(f"node {node_id} already exists")
        node = Node(node_id, self.wallet_address(node_id), self, self.params, self.genesis,
                    hashrate or self.default_hashrate, self.tick_ms)
        self.nodes[node_id] = node
        self.emit(EventType.NODE_CREATED, f"{node_id} joined the network (genesis {self.genesis.hash[:8]})", node_id)
        return node

    def remove_node(self, node_id: str) -> None:
        node = self._get(node_id)
        for other in sorted(self.neighbours(node_id)):
            self.disconnect(node_id, other, quiet=True)
        node.miner.stop("was removed")
        node.removed = True
        del self.nodes[node_id]
        self.emit(EventType.NODE_REMOVED, f"{node_id} left the network", node_id)

    def _get(self, node_id: str) -> Node:
        if node_id not in self.nodes:
            raise ValueError(f"unknown node '{node_id}'")
        return self.nodes[node_id]

    def neighbours(self, node_id: str) -> List[str]:
        out = []
        for a, b in self.links:
            if a == node_id:
                out.append(b)
            elif b == node_id:
                out.append(a)
        return sorted(out)

    def connect(self, a: str, b: str, instant: bool = False) -> None:
        """Open a link. ``instant`` skips the handshake (used for the initial topology)."""
        if a == b:
            raise ValueError("a node cannot connect to itself")
        na, nb = self._get(a), self._get(b)
        key = link_key(a, b)
        if key in self.links:
            raise ValueError(f"{a} and {b} are already connected")
        self.links[key] = {"created_ms": self.now_ms}
        na.peers.discard(b)
        if instant:
            for x, y in ((na, nb), (nb, na)):
                x.peers.add(y.id)
                x.ctx.emit(EventType.PEER_CONNECTED, f"{x.id} is now peered with {y.id}", x.id, {"peer": y.id})
            return
        if na.status == "online":
            na._send(b, MessageType.PEER_CONNECT, na._hello(reply=True))
        else:  # an offline node cannot handshake; it will when it comes back
            na.peers.add(b)

    def disconnect(self, a: str, b: str, quiet: bool = False) -> None:
        na, nb = self._get(a), self._get(b)
        key = link_key(a, b)
        if key not in self.links:
            raise ValueError(f"{a} and {b} are not connected")
        # courtesy goodbye travels even though the link is already gone
        for x, y in ((na, nb), (nb, na)):
            if x.status == "online":
                x._send(y.id, MessageType.PEER_DISCONNECT, {}, bypass=True)
        del self.links[key]
        na.on_link_lost(b)
        nb.on_link_lost(a)

    def set_online(self, node_id: str, online: bool) -> None:
        node = self._get(node_id)
        if online and node.status != "online":
            node.go_online()
        elif not online and node.status == "online":
            node.go_offline()

    def partition(self, groups: List[List[str]]) -> List[Tuple[str, str]]:
        member = {}
        for gi, group in enumerate(groups):
            for nid in group:
                self._get(nid)
                if nid in member:
                    raise ValueError(f"{nid} appears in more than one group")
                member[nid] = gi
        cut = [k for k in sorted(self.links) if k[0] in member and k[1] in member and member[k[0]] != member[k[1]]]
        for a, b in cut:
            self.disconnect(a, b)
        self.severed.extend(cut)
        self.emit(EventType.PARTITION, f"network partitioned into {len(groups)} groups; {len(cut)} link(s) cut",
                  None, {"groups": groups, "links": [list(k) for k in cut]})
        return cut

    def heal(self) -> List[Tuple[str, str]]:
        restored = []
        for a, b in self.severed:
            if a in self.nodes and b in self.nodes and link_key(a, b) not in self.links:
                self.connect(a, b)
                restored.append((a, b))
        self.severed = []
        self.emit(EventType.HEAL, f"network healed; {len(restored)} link(s) restored", None,
                  {"links": [list(k) for k in restored]})
        return restored

    def build_topology(self, kind: str = "ring", instant: bool = True) -> None:
        ids = sorted(self.nodes, key=lambda s: (len(s), s))
        n = len(ids)
        pairs: List[Tuple[str, str]] = []
        if kind == "full":
            pairs = [(ids[i], ids[j]) for i in range(n) for j in range(i + 1, n)]
        elif kind in ("ring", "ring+chord"):
            if n == 2:
                pairs = [(ids[0], ids[1])]
            elif n > 2:
                pairs = [(ids[i], ids[(i + 1) % n]) for i in range(n)]
            if kind == "ring+chord" and n >= 4:
                pairs.append((ids[0], ids[n // 2]))
        elif kind == "line":
            pairs = [(ids[i], ids[i + 1]) for i in range(n - 1)]
        elif kind != "none":
            raise ValueError(f"unknown topology '{kind}'")
        for a, b in pairs:
            if link_key(a, b) not in self.links:
                self.connect(a, b, instant=instant)

    # ---- queries -----------------------------------------------------
    def drain_feed(self) -> List[dict]:
        out, self.feed = self.feed, []
        return out

    def live_peer_count(self, node_id: str) -> int:
        return sum(1 for p in self.neighbours(node_id) if self.nodes[p].status == "online")
