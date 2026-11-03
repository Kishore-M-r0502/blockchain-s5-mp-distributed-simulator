"""Small hashing helpers shared by blocks, transactions and wallets."""
from __future__ import annotations

import hashlib
from typing import Iterable

ZERO_HASH = "0" * 64


def sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def merkle_root(tx_ids: Iterable[str]) -> str:
    """Bitcoin-style Merkle root over transaction ids (last node duplicated)."""
    level = [sha256_hex(t) for t in tx_ids]
    if not level:
        return sha256_hex("")
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [sha256_hex(level[i] + level[i + 1]) for i in range(0, len(level), 2)]
    return level[0]


def meets_difficulty(hash_hex: str, bits: int) -> bool:
    """True when ``hash_hex`` has at least ``bits`` leading zero *bits*."""
    if bits <= 0:
        return True
    return int(hash_hex, 16) >> (256 - bits) == 0


def short(h: str, n: int = 8) -> str:
    return h[:n] if h else ""
