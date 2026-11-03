"""Transactions and the account-ledger state they operate on.

Model: account based (like Ethereum) rather than UTXO. Every transaction carries a
per-sender ``nonce``; a transaction is only valid when its nonce equals the
sender's next expected nonce. That is what makes *double spending* concrete:
two different transactions with the same (sender, nonce) cannot both confirm.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from typing import Dict, Optional

from .hashing import sha256_hex
from .wallet import address_from_public_key, verify

COINBASE = "COINBASE"  # block reward, created by the miner
GENESIS = "GENESIS"  # initial allocations inside the genesis block
SYSTEM_SENDERS = (COINBASE, GENESIS)


@dataclass(frozen=True)
class Transaction:
    sender: str
    receiver: str
    amount: int
    nonce: int
    timestamp_ms: int  # simulation time
    public_key: str = ""
    signature: str = ""
    memo: str = ""

    def signing_payload(self) -> str:
        return f"{self.sender}|{self.receiver}|{self.amount}|{self.nonce}|{self.timestamp_ms}|{self.memo}"

    @cached_property
    def id(self) -> str:
        # The signature is deliberately not part of the id (no malleability).
        return sha256_hex(self.signing_payload())

    @property
    def is_system(self) -> bool:
        return self.sender in SYSTEM_SENDERS

    def stateless_error(self) -> Optional[str]:
        """Checks that need no ledger: shape, address binding and signature."""
        if self.is_system:
            return None
        if not isinstance(self.amount, int) or isinstance(self.amount, bool) or self.amount <= 0:
            return "amount must be a positive integer"
        if not isinstance(self.nonce, int) or self.nonce < 0:
            return "nonce must be a non-negative integer"
        if not self.receiver:
            return "missing receiver"
        if self.sender == self.receiver:
            return "sender and receiver are identical"
        if not self.public_key or not self.signature:
            return "missing public key or signature"
        if address_from_public_key(self.public_key) != self.sender:
            return "sender address does not match public key"
        if not verify(self.public_key, self.signing_payload(), self.signature):
            return "invalid signature"
        return None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "sender": self.sender, "receiver": self.receiver,
            "amount": self.amount, "nonce": self.nonce, "timestamp_ms": self.timestamp_ms,
            "public_key": self.public_key, "signature": self.signature, "memo": self.memo,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transaction":
        return cls(
            sender=str(d["sender"]), receiver=str(d["receiver"]), amount=d["amount"],
            nonce=d["nonce"], timestamp_ms=int(d["timestamp_ms"]),
            public_key=str(d.get("public_key", "")), signature=str(d.get("signature", "")),
            memo=str(d.get("memo", "")),
        )


class LedgerState:
    """Balances and next-expected nonces after applying a prefix of the chain."""

    def __init__(self, balances: Optional[Dict[str, int]] = None, nonces: Optional[Dict[str, int]] = None):
        self.balances: Dict[str, int] = dict(balances or {})
        self.nonces: Dict[str, int] = dict(nonces or {})

    def copy(self) -> "LedgerState":
        return LedgerState(self.balances, self.nonces)

    def balance(self, addr: str) -> int:
        return self.balances.get(addr, 0)

    def next_nonce(self, addr: str) -> int:
        return self.nonces.get(addr, 0)

    def credit(self, addr: str, amount: int) -> None:
        self.balances[addr] = self.balance(addr) + amount

    def check(self, tx: Transaction) -> Optional[str]:
        expected = self.next_nonce(tx.sender)
        if tx.nonce < expected:
            return "nonce already confirmed on chain (double spend / replay)"
        if tx.nonce > expected:
            return f"nonce gap (expected {expected}, got {tx.nonce})"
        if tx.amount > self.balance(tx.sender):
            return f"insufficient balance (has {self.balance(tx.sender)}, needs {tx.amount})"
        return None

    def apply(self, tx: Transaction) -> Optional[str]:
        err = self.check(tx)
        if err:
            return err
        self.balances[tx.sender] = self.balance(tx.sender) - tx.amount
        self.credit(tx.receiver, tx.amount)
        self.nonces[tx.sender] = tx.nonce + 1
        return None
