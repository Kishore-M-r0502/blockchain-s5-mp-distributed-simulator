"""A node's private block store: a tree of blocks plus a best-chain pointer."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

from . import consensus
from .block import Block
from .consensus import ConsensusParams
from .transaction import LedgerState


class Outcome(str, Enum):
    ADDED = "ADDED"  # extended the best chain
    SIDE_BRANCH = "SIDE_BRANCH"  # valid, stored, but not (yet) the best chain
    REORG = "REORG"  # caused the best chain to switch branches
    DUPLICATE = "DUPLICATE"
    ORPHAN = "ORPHAN"  # parent unknown
    INVALID = "INVALID"


@dataclass
class AddResult:
    outcome: Outcome
    reason: str = ""
    block: Optional[Block] = None


class Blockchain:
    def __init__(self, genesis: Block, params: ConsensusParams):
        self.params = params
        self.genesis = genesis
        self.blocks: Dict[str, Block] = {genesis.hash: genesis}
        self.states: Dict[str, LedgerState] = {genesis.hash: consensus.genesis_state(genesis)}
        self.tip: str = genesis.hash
        self.arrival: Dict[str, int] = {genesis.hash: 0}  # insertion order, for stable displays

    # ---- views -------------------------------------------------------
    @property
    def latest(self) -> Block:
        return self.blocks[self.tip]

    @property
    def height(self) -> int:
        return self.latest.index

    @property
    def tip_state(self) -> LedgerState:
        return self.states[self.tip]

    @property
    def chain(self) -> List[Block]:
        out, h = [], self.tip
        while True:
            b = self.blocks[h]
            out.append(b)
            if b.index == 0:
                break
            h = b.previous_hash
        return out[::-1]

    def best_hashes(self) -> Set[str]:
        return {b.hash for b in self.chain}

    def side_tips(self) -> List[Block]:
        """Leaf blocks that are not on the best chain (competing branches)."""
        parents = {b.previous_hash for b in self.blocks.values()}
        best = self.best_hashes()
        return [b for b in self.blocks.values() if b.hash not in parents and b.hash not in best]

    # ---- mutation ----------------------------------------------------
    def add_block(self, block: Block) -> AddResult:
        if block.hash in self.blocks:
            return AddResult(Outcome.DUPLICATE, "block already known", block)
        parent = self.blocks.get(block.previous_hash)
        if parent is None:
            return AddResult(Outcome.ORPHAN, "parent block unknown", block)
        err, state = consensus.validate_block(block, parent, self.states[parent.hash], self.params)
        if err:
            return AddResult(Outcome.INVALID, err, block)
        self.blocks[block.hash] = block
        self.states[block.hash] = state
        self.arrival[block.hash] = len(self.arrival)
        if not consensus.is_better(block, self.latest):
            return AddResult(Outcome.SIDE_BRANCH, "valid block on a non-best branch", block)
        extends = block.previous_hash == self.tip
        self.tip = block.hash
        return AddResult(Outcome.ADDED if extends else Outcome.REORG, "", block)

    # ---- verification ------------------------------------------------
    def verify_integrity(self) -> dict:
        """Re-validate the whole best chain from scratch (ignores cached states)."""
        ok, error, index = consensus.validate_chain(self.chain, self.params)
        return {"valid": ok, "error": error, "bad_index": index, "height": self.height}

    def verify_block(self, index: int) -> dict:
        chain = self.chain
        if not 0 <= index < len(chain):
            raise IndexError(f"no block at height {index}")
        b = chain[index]
        checks = [("hash matches block contents", b.has_valid_hash())]
        if index == 0:
            checks.append(("genesis has no previous block", b.previous_hash == "0" * 64))
        else:
            parent = chain[index - 1]
            checks += [
                ("links to previous block hash", b.previous_hash == parent.hash),
                ("proof of work meets difficulty", b.has_valid_pow()),
                (f"difficulty equals required {self.params.difficulty_for(index)}", b.difficulty == self.params.difficulty_for(index)),
                ("timestamp not before parent", b.timestamp_ms >= parent.timestamp_ms),
            ]
            err, _ = consensus.validate_block(b, parent, self._state_before(chain, index), self.params)
            checks.append(("transactions valid (signatures, nonces, balances)", err is None))
        return {"index": index, "hash": b.hash, "valid": all(ok for _, ok in checks),
                "checks": [{"check": c, "ok": ok} for c, ok in checks]}

    def _state_before(self, chain: List[Block], index: int) -> LedgerState:
        state = consensus.genesis_state(chain[0])
        for parent, block in zip(chain[:index], chain[1:index]):
            _, s = consensus.validate_block(block, parent, state, self.params)
            if s is None:  # an earlier block is broken; fall back to cached state
                return self.states.get(chain[index - 1].hash, state)
            state = s
        return state
