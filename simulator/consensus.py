"""Consensus rules: block validity, fork choice and reorganisation helpers.

Educational consensus (see README "Consensus mechanism"): the valid chain with the
most blocks wins (the "longest chain" rule). Ties keep the chain a node saw first.
This is simpler than Bitcoin, which compares accumulated *work*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .block import Block
from .hashing import ZERO_HASH
from .transaction import COINBASE, GENESIS, LedgerState, Transaction

CONSENSUS_ASSUMPTIONS = [
    "Fork choice is the longest valid chain (block count). Real protocols such as Bitcoin pick the chain with the most accumulated work.",
    "On equal length a node keeps the chain it saw first, so competing tips persist until one branch grows.",
    "Difficulty is a network-wide schedule keyed by block height; changing it affects blocks from the next height upwards. There is no automatic retargeting.",
    "Every node uses the same hard-coded rules and the same genesis block; there is no software-version split.",
    "No block rewards halving, no fees, no coinbase maturity: a reward is spendable immediately.",
    "Longest chain is not automatically the most secure chain: an attacker with a majority of hash power can out-mine honest nodes (51% attack). The simulator does not model attackers' economics.",
    "Signatures are educational (Schnorr on secp256k1 in pure Python) and keys are derived from the simulation seed. Do not reuse this code for real funds.",
]


@dataclass
class ConsensusParams:
    block_reward: int = 50
    max_txs_per_block: int = 20
    difficulty_schedule: List[Tuple[int, int]] = field(default_factory=lambda: [(0, 10)])  # (from height, bits)

    def difficulty_for(self, index: int) -> int:
        bits = self.difficulty_schedule[0][1]
        for start, value in self.difficulty_schedule:
            if start <= index:
                bits = value
        return bits

    def set_difficulty(self, from_index: int, bits: int) -> None:
        self.difficulty_schedule = [(s, b) for s, b in self.difficulty_schedule if s < from_index]
        self.difficulty_schedule.append((from_index, bits))


def make_coinbase(miner_address: str, index: int, timestamp_ms: int, reward: int) -> Transaction:
    return Transaction(COINBASE, miner_address, reward, index, timestamp_ms, memo=f"height-{index}")


def make_genesis(allocations: Dict[str, int]) -> Block:
    txs = [Transaction(GENESIS, addr, amt, i, 0, memo="genesis") for i, (addr, amt) in enumerate(sorted(allocations.items()))]
    return Block(0, 0, txs, ZERO_HASH, 0).seal()


def genesis_state(genesis: Block) -> LedgerState:
    state = LedgerState()
    for tx in genesis.transactions:
        state.credit(tx.receiver, tx.amount)
    return state


def validate_block(block: Block, parent: Block, parent_state: LedgerState,
                   params: ConsensusParams) -> Tuple[Optional[str], Optional[LedgerState]]:
    """Return (error, None) or (None, state_after_block)."""
    if block.index != parent.index + 1:
        return f"bad height {block.index}, expected {parent.index + 1}", None
    if block.previous_hash != parent.hash:
        return "previous hash does not match parent block", None
    if not block.has_valid_hash():
        return "block hash does not match block contents", None
    required = params.difficulty_for(block.index)
    if block.difficulty != required:
        return f"difficulty {block.difficulty} differs from required {required}", None
    if not block.has_valid_pow():
        return f"proof of work does not meet difficulty {required}", None
    if block.timestamp_ms < parent.timestamp_ms:
        return "timestamp is earlier than the parent block", None
    txs = block.transactions
    if not txs or txs[0].sender != COINBASE:
        return "first transaction must be the coinbase", None
    cb = txs[0]
    if cb.amount != params.block_reward:
        return f"coinbase pays {cb.amount}, allowed reward is {params.block_reward}", None
    if not cb.receiver or cb.memo != f"height-{block.index}":
        return "malformed coinbase", None
    if len(txs) - 1 > params.max_txs_per_block:
        return "too many transactions in block", None
    ids = [t.id for t in txs]
    if len(set(ids)) != len(ids):
        return "duplicate transaction inside block", None
    state = parent_state.copy()
    state.credit(cb.receiver, cb.amount)
    for tx in txs[1:]:
        if tx.is_system:
            return "only the first transaction may be a coinbase", None
        err = tx.stateless_error() or state.apply(tx)
        if err:
            return f"transaction {tx.id[:8]}: {err}", None
    return None, state


def is_better(candidate: Block, current: Block) -> bool:
    """Longest-chain rule. Equal length does NOT displace the current tip."""
    return candidate.index > current.index


def reorg_path(blocks: Dict[str, Block], old_tip: str, new_tip: str) -> Tuple[Block, List[Block], List[Block]]:
    """Return (fork_point, orphaned_blocks, adopted_blocks), both lists oldest first."""
    a, b = blocks[old_tip], blocks[new_tip]
    orphaned: List[Block] = []
    adopted: List[Block] = []
    while a.hash != b.hash:
        if a.index >= b.index:
            orphaned.append(a)
            a = blocks[a.previous_hash]
        else:
            adopted.append(b)
            b = blocks[b.previous_hash]
    return a, orphaned[::-1], adopted[::-1]


def validate_chain(chain: List[Block], params: ConsensusParams) -> Tuple[bool, str, int]:
    """Independently re-validate a full chain (genesis first). Returns (ok, error, index)."""
    if not chain:
        return False, "empty chain", 0
    genesis = chain[0]
    if genesis.index != 0 or genesis.previous_hash != ZERO_HASH or not genesis.has_valid_hash():
        return False, "invalid genesis block", 0
    state = genesis_state(genesis)
    for parent, block in zip(chain, chain[1:]):
        err, state = validate_block(block, parent, state, params)
        if err:
            return False, err, block.index
    return True, "", -1
