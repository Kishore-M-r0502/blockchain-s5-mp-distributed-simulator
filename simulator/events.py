"""Append-only network event log (timestamps are simulation time)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional


class EventType:
    NODE_CREATED = "NODE_CREATED"
    NODE_REMOVED = "NODE_REMOVED"
    NODE_STATUS = "NODE_STATUS"
    PEER_CONNECTED = "PEER_CONNECTED"
    PEER_DISCONNECTED = "PEER_DISCONNECTED"
    TX_CREATED = "TX_CREATED"
    TX_BROADCAST = "TX_BROADCAST"
    TX_RECEIVED = "TX_RECEIVED"
    TX_REJECTED = "TX_REJECTED"
    BLOCK_MINED = "BLOCK_MINED"
    BLOCK_BROADCAST = "BLOCK_BROADCAST"
    BLOCK_RECEIVED = "BLOCK_RECEIVED"
    BLOCK_REJECTED = "BLOCK_REJECTED"
    FORK_DETECTED = "FORK_DETECTED"
    CHAIN_REORG = "CHAIN_REORG"
    CHAIN_SYNC = "CHAIN_SYNC"
    MINING_STARTED = "MINING_STARTED"
    MINING_STOPPED = "MINING_STOPPED"
    MESSAGE_DROPPED = "MESSAGE_DROPPED"
    DIFFICULTY_CHANGED = "DIFFICULTY_CHANGED"
    LATENCY_CHANGED = "LATENCY_CHANGED"
    PARTITION = "PARTITION"
    HEAL = "HEAL"
    SIM_PAUSED = "SIM_PAUSED"
    SIM_RESUMED = "SIM_RESUMED"
    SIM_RESET = "SIM_RESET"
    SCENARIO = "SCENARIO"
    ERROR = "ERROR"


@dataclass
class Event:
    id: int
    time_ms: int
    type: str
    message: str
    node: Optional[str] = None
    data: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "time_ms": self.time_ms, "type": self.type,
                "message": self.message, "node": self.node, "data": self.data}


class EventLog:
    def __init__(self, max_events: int = 5000):
        self._events: Deque[Event] = deque(maxlen=max_events)
        self._next_id = 1

    @property
    def last_id(self) -> int:
        return self._next_id - 1

    @property
    def next_id(self) -> int:
        return self._next_id

    def emit(self, time_ms: int, type_: str, message: str, node: Optional[str] = None,
             data: Optional[Dict[str, Any]] = None) -> Event:
        ev = Event(self._next_id, time_ms, type_, message, node, data or {})
        self._next_id += 1
        self._events.append(ev)
        return ev

    def since(self, last_id: int = 0, limit: Optional[int] = None) -> List[Event]:
        out = [e for e in self._events if e.id > last_id]
        return out[-limit:] if limit else out

    def tail(self, n: int) -> List[Event]:
        return list(self._events)[-n:]

    def clear(self) -> None:
        """Drop stored events; ids keep increasing so client cursors stay valid."""
        self._events.clear()

    def __len__(self) -> int:
        return len(self._events)
