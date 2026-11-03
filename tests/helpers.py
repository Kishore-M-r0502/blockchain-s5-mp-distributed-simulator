from simulator.block import Block
from simulator.consensus import ConsensusParams, make_coinbase, make_genesis
from simulator.miner import proof_of_work
from simulator.wallet import Wallet

BITS = 8


def params(bits=BITS):
    return ConsensusParams(difficulty_schedule=[(0, bits)])


def setup(balance=100):
    alice, bob, miner = Wallet("alice", 1), Wallet("bob", 1), Wallet("miner", 1)
    genesis = make_genesis({alice.address: balance, bob.address: balance})
    return alice, bob, miner, genesis


def mine_on(parent, miner, txs=(), p=None, ts=None):
    p = p or params()
    ts = parent.timestamp_ms + 1000 if ts is None else ts
    block = Block(parent.index + 1, ts, [make_coinbase(miner.address, parent.index + 1, ts, p.block_reward)] + list(txs),
                  parent.hash, p.difficulty_for(parent.index + 1))
    proof_of_work(block)
    return block
