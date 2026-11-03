"""The block data structure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .hashing import meets_difficulty, merkle_root, sha256_hex
from .transaction import COINBASE, Transaction


@dataclass
class Block:
    index: int
    timestamp_ms: int  # simulation time, not wall-clock time
    transactions: List[Transaction]
    previous_hash: str
    difficulty: int  # required number of leading zero bits
    nonce: int = 0
    hash: str = ""

    def merkle(self) -> str:
        return merkle_root(t.id for t in self.transactions)

    def header_prefix(self) -> str:
        return f"{self.index}|{self.timestamp_ms}|{self.previous_hash}|{self.merkle()}|{self.difficulty}|"

    def compute_hash(self) -> str:
        return sha256_hex(self.header_prefix() + str(self.nonce))

    def seal(self) -> "Block":
        self.hash = self.compute_hash()
        return self

    def has_valid_hash(self) -> bool:
        return self.hash == self.compute_hash()

    def has_valid_pow(self) -> bool:
        return meets_difficulty(self.hash, self.difficulty)

    @property
    def miner_address(self) -> str:
        if self.transactions and self.transactions[0].sender == COINBASE:
            return self.transactions[0].receiver
        return ""

    def to_dict(self) -> dict:
        return {
            "index": self.index, "timestamp_ms": self.timestamp_ms,
            "previous_hash": self.previous_hash, "hash": self.hash,
            "nonce": self.nonce, "difficulty": self.difficulty,
            "merkle_root": self.merkle(),
            "transactions": [t.to_dict() for t in self.transactions],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Block":
        return cls(
            index=int(d["index"]), timestamp_ms=int(d["timestamp_ms"]),
            transactions=[Transaction.from_dict(t) for t in d["transactions"]],
            previous_hash=str(d["previous_hash"]), difficulty=int(d["difficulty"]),
            nonce=int(d["nonce"]), hash=str(d["hash"]),
        )
