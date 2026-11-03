"""Educational wallet: Schnorr signatures over secp256k1, standard library only.

This is a *teaching* implementation. It is correct enough to demonstrate
public-key signatures (only the key holder can authorise a spend, anyone can
verify), but it is NOT production-grade: it is not constant-time, uses affine
arithmetic, and derives keys deterministically from (seed, name) so that the
simulation is reproducible. Never use it to protect real funds.

Private keys never leave the Wallet object: the public API only exposes the
name, public key and address.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional, Tuple

from .hashing import sha256_hex

P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)

Point = Optional[Tuple[int, int]]


def _add(p1: Point, p2: Point) -> Point:
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % P == 0:
            return None
        lam = 3 * x1 * x1 * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    return x3, (lam * (x1 - x3) - y1) % P


def _mul(k: int, pt: Point) -> Point:
    result: Point = None
    addend = pt
    while k:
        if k & 1:
            result = _add(result, addend)
        addend = _add(addend, addend)
        k >>= 1
    return result


def _on_curve(pt: Point) -> bool:
    if pt is None:
        return False
    x, y = pt
    return 0 < x < P and 0 < y < P and (y * y - x * x * x - 7) % P == 0


def _encode(pt: Tuple[int, int]) -> str:
    return f"{pt[0]:064x}{pt[1]:064x}"


def _decode(h: str) -> Point:
    if len(h) != 128:
        return None
    try:
        return int(h[:64], 16), int(h[64:], 16)
    except ValueError:
        return None


def _challenge(r_hex: str, pub_hex: str, message: str) -> int:
    return int(sha256_hex(r_hex + pub_hex + message), 16) % N


def address_from_public_key(public_key_hex: str) -> str:
    return "0x" + sha256_hex(public_key_hex)[:40]


@lru_cache(maxsize=65536)
def verify(public_key_hex: str, message: str, signature_hex: str) -> bool:
    """Verify a Schnorr signature (pure function, so results are cacheable)."""
    if len(signature_hex) != 192:
        return False
    pub = _decode(public_key_hex)
    r_pt = _decode(signature_hex[:128])
    try:
        s = int(signature_hex[128:], 16)
    except ValueError:
        return False
    if not (_on_curve(pub) and _on_curve(r_pt)) or not 0 < s < N:
        return False
    e = _challenge(signature_hex[:128], public_key_hex, message)
    return _mul(s, G) == _add(r_pt, _mul(e, pub))


class Wallet:
    """A named key pair derived deterministically from ``(seed, name)``."""

    def __init__(self, name: str, seed: int = 0):
        self.name = name
        self.__d = int(sha256_hex(f"wallet|{seed}|{name}"), 16) % (N - 1) + 1
        self.public_key = _encode(_mul(self.__d, G))
        self.address = address_from_public_key(self.public_key)

    def sign(self, message: str) -> str:
        # Deterministic nonce (RFC 6979 spirit): same message -> same signature.
        k = int(sha256_hex(f"{self.__d:064x}|{message}"), 16) % (N - 1) + 1
        r_hex = _encode(_mul(k, G))
        e = _challenge(r_hex, self.public_key, message)
        s = (k + e * self.__d) % N
        return r_hex + f"{s:064x}"

    def create_transaction(self, receiver: str, amount: int, nonce: int, timestamp_ms: int):
        from .transaction import Transaction  # local import avoids a cycle

        unsigned = Transaction(self.address, receiver, amount, nonce, timestamp_ms, self.public_key)
        sig = self.sign(unsigned.signing_payload())
        return Transaction(self.address, receiver, amount, nonce, timestamp_ms, self.public_key, sig)

    def public_info(self) -> dict:
        return {"name": self.name, "address": self.address, "public_key": self.public_key}

    def __repr__(self) -> str:  # never leak the secret scalar
        return f"Wallet({self.name!r}, {self.address})"
