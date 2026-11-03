"""Per-node pool of valid, not-yet-confirmed transactions."""
from __future__ import annotations

from collections import deque
from typing import Deque, Dict, List, Tuple

from .transaction import LedgerState, Transaction


class Mempool:
    def __init__(self, max_rejected: int = 200):
        self._txs: Dict[str, Transaction] = {}
        self._slots: Dict[Tuple[str, int], str] = {}  # (sender, nonce) -> tx id
        self.rejected: Deque[dict] = deque(maxlen=max_rejected)

    def __len__(self) -> int:
        return len(self._txs)

    def __contains__(self, tx_id: str) -> bool:
        return tx_id in self._txs

    def all(self) -> List[Transaction]:
        return list(self._txs.values())

    def _record_rejection(self, tx: Transaction, reason: str, now_ms: int) -> None:
        self.rejected.append({"tx": tx.to_dict(), "reason": reason, "time_ms": now_ms})

    def add(self, tx: Transaction, state: LedgerState, now_ms: int = 0) -> Tuple[bool, str]:
        """Validate ``tx`` against the tip state and pool it. Returns (ok, reason)."""
        if tx.id in self._txs:
            return False, "duplicate transaction"
        err = "system transactions cannot enter the mempool" if tx.is_system else tx.stateless_error()
        if err is None and tx.nonce < state.next_nonce(tx.sender):
            err = "nonce already confirmed on chain (double spend / replay)"
        if err is None and (tx.sender, tx.nonce) in self._slots:
            err = f"double spend: conflicts with pending tx {self._slots[(tx.sender, tx.nonce)][:8]}"
        if err is None and tx.amount > state.balance(tx.sender):
            err = f"insufficient balance (has {state.balance(tx.sender)}, needs {tx.amount})"
        if err:
            self._record_rejection(tx, err, now_ms)
            return False, err
        self._txs[tx.id] = tx
        self._slots[(tx.sender, tx.nonce)] = tx.id
        return True, ""

    def _drop(self, tx_id: str) -> None:
        tx = self._txs.pop(tx_id, None)
        if tx is not None and self._slots.get((tx.sender, tx.nonce)) == tx_id:
            del self._slots[(tx.sender, tx.nonce)]

    def remove_confirmed(self, confirmed: List[Transaction]) -> None:
        for tx in confirmed:
            self._drop(tx.id)

    def revalidate(self, state: LedgerState, now_ms: int = 0) -> List[Tuple[Transaction, str]]:
        """Evict transactions that became invalid (e.g. nonce used by a rival tx)."""
        dropped = []
        for tx in list(self._txs.values()):
            if tx.nonce < state.next_nonce(tx.sender):
                reason = "superseded: nonce confirmed by a different transaction (double spend)"
                self._drop(tx.id)
                self._record_rejection(tx, reason, now_ms)
                dropped.append((tx, reason))
        return dropped

    def select_for_block(self, state: LedgerState, limit: int) -> List[Transaction]:
        """Pick transactions in arrival order that apply cleanly, honouring nonces."""
        working = state.copy()
        chosen: List[Transaction] = []
        chosen_ids = set()
        progress = True
        while progress and len(chosen) < limit:
            progress = False
            for tx in self._txs.values():
                if tx.id in chosen_ids:
                    continue
                if working.check(tx) is None:
                    working.apply(tx)
                    chosen.append(tx)
                    chosen_ids.add(tx.id)
                    progress = True
                    if len(chosen) >= limit:
                        break
        return chosen
