"""Proof of Work and the per-node mining process.

Mining is *real*: the miner hashes ``header || nonce`` with SHA-256 until the hash
has the required number of leading zero bits. What is simulated is the speed: a
miner owns a ``hashrate`` (attempts per simulated second). Finding a solution after
``k`` attempts therefore takes ``k * 1000 / hashrate`` simulated milliseconds,
independent of how fast the host CPU is.
"""
from __future__ import annotations

import hashlib
from typing import Optional, Tuple

from .block import Block
from .consensus import make_coinbase
from .events import EventType


def search_nonce(prefix: str, bits: int, start: int, count: int) -> Tuple[Optional[int], Optional[str], int]:
    """Try ``count`` nonces from ``start``. Returns (nonce, hash, attempts_used)."""
    target = 1 << (256 - bits) if bits > 0 else 1 << 256
    base = hashlib.sha256(prefix.encode())
    for nonce in range(start, start + count):
        h = base.copy()
        h.update(str(nonce).encode())
        digest = h.digest()
        if int.from_bytes(digest, "big") < target:
            return nonce, digest.hex(), nonce - start + 1
    return None, None, count


def proof_of_work(block: Block, start_nonce: int = 0, max_attempts: Optional[int] = None) -> int:
    """Synchronously mine ``block`` in place. Returns the number of attempts."""
    prefix, attempts, nonce = block.header_prefix(), 0, start_nonce
    while True:
        chunk = 4096 if max_attempts is None else min(4096, max_attempts - attempts)
        if chunk <= 0:
            raise RuntimeError("proof of work gave up")
        found, digest, used = search_nonce(prefix, block.difficulty, nonce, chunk)
        attempts += used
        if found is not None:
            block.nonce, block.hash = found, digest
            return attempts
        nonce += chunk


class Miner:
    def __init__(self, node, hashrate: int, tick_ms: int):
        self.node = node
        self.hashrate = hashrate
        self.tick_ms = tick_ms
        self.active = False  # mining requested
        self.blocks_remaining: Optional[int] = None  # None = keep mining
        self.epoch = 0  # bumped whenever the current job becomes stale
        self.job: Optional[dict] = None
        self.total_attempts = 0
        self.blocks_mined = 0
        self.last_duration_ms: Optional[int] = None
        self.last_attempts: Optional[int] = None

    # ---- control -----------------------------------------------------
    def start(self, blocks: Optional[int] = None) -> None:
        self.active = True
        self.blocks_remaining = blocks
        what = "continuously" if blocks is None else f"until {blocks} block(s) are found"
        self.node.ctx.emit(EventType.MINING_STARTED, f"{self.node.id} started mining {what}", self.node.id)
        self.refresh(force=True)

    def stop(self, reason: str = "stopped") -> None:
        was = self.active
        self.active = False
        self.blocks_remaining = None
        self.epoch += 1
        self.job = None
        if was:
            self.node.ctx.emit(EventType.MINING_STOPPED, f"{self.node.id} {reason}", self.node.id)

    def suspend(self) -> None:
        """Drop the current job (node went offline) but remember we should mine."""
        self.epoch += 1
        self.job = None

    def refresh(self, force: bool = False) -> None:
        """Build a fresh block template on top of the node's best tip."""
        node = self.node
        if not self.active or node.status != "online" or node.removed:
            return
        if self.job and self.job.get("found") and not force:
            return  # a solution is already waiting to be published
        bc, params = node.blockchain, node.params
        tip, now = bc.latest, node.ctx.now_ms
        ts = max(now, tip.timestamp_ms)
        txs = node.mempool.select_for_block(bc.tip_state, params.max_txs_per_block)
        coinbase = make_coinbase(node.address, tip.index + 1, ts, params.block_reward)
        block = Block(tip.index + 1, ts, [coinbase] + txs, tip.hash, params.difficulty_for(tip.index + 1))
        self.epoch += 1
        self.job = {"block": block, "prefix": block.header_prefix(), "next_nonce": 0,
                    "attempts": 0, "started_ms": now, "found": None}
        epoch = self.epoch
        node.ctx.schedule(0, lambda: self._tick(epoch))

    # ---- mining loop -------------------------------------------------
    def _tick(self, epoch: int) -> None:
        if epoch != self.epoch or not self.active or self.job is None:
            return
        job = self.job
        count = max(1, self.hashrate * self.tick_ms // 1000)
        nonce, digest, used = search_nonce(job["prefix"], job["block"].difficulty, job["next_nonce"], count)
        job["attempts"] += used
        self.total_attempts += used
        if nonce is not None:
            job["found"] = (nonce, digest)
            delay = used * 1000 // max(1, self.hashrate)
            self.node.ctx.schedule(delay, lambda: self._complete(epoch))
        else:
            job["next_nonce"] += count
            self.node.ctx.schedule(self.tick_ms, lambda: self._tick(epoch))

    def _complete(self, epoch: int) -> None:
        if epoch != self.epoch or not self.active or not self.job or not self.job["found"]:
            return  # the tip moved while we were hashing: solution is stale
        job, now = self.job, self.node.ctx.now_ms
        block = job["block"]
        block.nonce, block.hash = job["found"]
        duration = now - job["started_ms"]
        self.blocks_mined += 1
        self.last_duration_ms, self.last_attempts = duration, job["attempts"]
        self.job = None
        self.epoch += 1
        stop_after = False
        if self.blocks_remaining is not None:
            self.blocks_remaining -= 1
            if self.blocks_remaining <= 0:
                self.active, self.blocks_remaining, stop_after = False, None, True
        self.node.on_block_mined(block, duration, job["attempts"])
        if stop_after:
            self.node.ctx.emit(EventType.MINING_STOPPED, f"{self.node.id} reached its block target", self.node.id)

    def info(self) -> dict:
        job = self.job
        diff = job["block"].difficulty if job else self.node.params.difficulty_for(self.node.blockchain.height + 1)
        return {
            "active": self.active, "hashrate": self.hashrate, "difficulty": diff,
            "expected_attempts": 2 ** diff,
            "job_attempts": job["attempts"] if job else 0,
            "job_nonce": job["next_nonce"] if job else 0,
            "job_started_ms": job["started_ms"] if job else None,
            "job_height": job["block"].index if job else None,
            "total_attempts": self.total_attempts, "blocks_mined": self.blocks_mined,
            "last_duration_ms": self.last_duration_ms, "last_attempts": self.last_attempts,
            "blocks_remaining": self.blocks_remaining,
        }
