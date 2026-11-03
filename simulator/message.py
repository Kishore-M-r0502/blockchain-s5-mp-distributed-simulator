"""Network message model."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class MessageType(str, Enum):
    TRANSACTION = "TRANSACTION"
    BLOCK = "BLOCK"
    CHAIN_REQUEST = "CHAIN_REQUEST"
    CHAIN_RESPONSE = "CHAIN_RESPONSE"
    PEER_CONNECT = "PEER_CONNECT"
    PEER_DISCONNECT = "PEER_DISCONNECT"


def describe_payload(mtype: MessageType, payload: Dict[str, Any]) -> str:
    """Short human-readable payload summary (never the bulky raw payload)."""
    if mtype == MessageType.TRANSACTION:
        return f"tx {payload['tx']['id'][:8]}"
    if mtype == MessageType.BLOCK:
        b = payload["block"]
        return f"block #{b['index']} {b['hash'][:8]}"
    if mtype == MessageType.CHAIN_REQUEST:
        return f"have height {payload.get('height', 0)}"
    if mtype == MessageType.CHAIN_RESPONSE:
        return f"{len(payload.get('blocks', []))} blocks"
    if mtype == MessageType.PEER_CONNECT:
        return f"handshake, height {payload.get('height', 0)}"
    return "goodbye"


@dataclass
class Message:
    id: str
    sender: str
    receiver: str
    type: MessageType
    timestamp_ms: int  # simulation time at which it was sent
    payload: Dict[str, Any] = field(default_factory=dict)
    deliver_at_ms: int = 0
    status: str = "in_flight"  # in_flight | delivered | dropped
    drop_reason: str = ""

    def summary(self) -> dict:
        return {
            "id": self.id, "type": self.type.value, "from": self.sender, "to": self.receiver,
            "send_ms": self.timestamp_ms, "deliver_ms": self.deliver_at_ms,
            "latency_ms": self.deliver_at_ms - self.timestamp_ms,
            "status": self.status, "reason": self.drop_reason,
            "hint": describe_payload(self.type, self.payload),
        }
