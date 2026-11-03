import dataclasses
import unittest

from simulator import consensus
from simulator.block import Block
from simulator.blockchain import Blockchain, Outcome
from simulator.miner import proof_of_work
from tests.helpers import mine_on, params, setup


class BlockchainTests(unittest.TestCase):
    def setUp(self):
        self.alice, self.bob, self.miner, self.genesis = setup()
        self.p = params()
        self.bc = Blockchain(Block.from_dict(self.genesis.to_dict()), self.p)

    def extend(self, n, parent=None, txs=()):
        parent, out = parent or self.bc.latest, []
        for i in range(n):
            parent = mine_on(parent, self.miner, txs if i == 0 else (), self.p)
            out.append(parent)
        return out

    def test_valid_blocks_extend_chain_and_update_balances(self):
        tx = self.alice.create_transaction(self.bob.address, 30, 0, 1)
        [b1] = self.extend(1, txs=[tx])
        self.assertEqual(self.bc.add_block(b1).outcome, Outcome.ADDED)
        s = self.bc.tip_state
        self.assertEqual((s.balance(self.alice.address), s.balance(self.bob.address), s.balance(self.miner.address)), (70, 130, 50))

    def test_duplicate_and_orphan(self):
        [b1, b2] = self.extend(2)
        self.assertEqual(self.bc.add_block(b2).outcome, Outcome.ORPHAN)
        self.assertEqual(self.bc.add_block(b1).outcome, Outcome.ADDED)
        self.assertEqual(self.bc.add_block(b1).outcome, Outcome.DUPLICATE)

    def reject(self, block, text):
        res = self.bc.add_block(block)
        self.assertEqual(res.outcome, Outcome.INVALID)
        self.assertIn(text, res.reason)
        self.assertEqual(self.bc.height, 0)

    def test_invalid_blocks_are_rejected(self):
        [good] = self.extend(1)
        bad_hash = Block.from_dict(good.to_dict())
        bad_hash.nonce += 1
        self.reject(bad_hash, "hash does not match")
        weak = Block(1, 1000, good.transactions, self.genesis.hash, self.p.difficulty_for(1))
        weak.nonce = 0
        while True:
            weak.seal()
            if not weak.has_valid_pow():
                break
            weak.nonce += 1
        self.reject(weak, "proof of work")
        easy = Block(1, 1000, good.transactions, self.genesis.hash, 1)
        proof_of_work(easy)
        self.reject(easy, "difficulty")
        greedy = Block(1, 1000, [consensus.make_coinbase(self.miner.address, 1, 1000, 5000)], self.genesis.hash, self.p.difficulty_for(1))
        proof_of_work(greedy)
        self.reject(greedy, "coinbase")
        over = self.alice.create_transaction(self.bob.address, 10 ** 6, 0, 1)
        blk = Block(1, 1000, [consensus.make_coinbase(self.miner.address, 1, 1000, 50), over], self.genesis.hash, self.p.difficulty_for(1))
        proof_of_work(blk)
        self.reject(blk, "insufficient balance")
        forged = dataclasses.replace(self.alice.create_transaction(self.bob.address, 5, 0, 1), amount=6)
        blk = Block(1, 1000, [consensus.make_coinbase(self.miner.address, 1, 1000, 50), forged], self.genesis.hash, self.p.difficulty_for(1))
        proof_of_work(blk)
        self.reject(blk, "invalid signature")
        early = Block(1, 0, good.transactions[:1], self.genesis.hash, self.p.difficulty_for(1))
        self.assertEqual(self.bc.add_block(early).outcome, Outcome.INVALID)

    def test_same_height_competitor_is_side_branch_and_longer_chain_reorgs(self):
        main = self.extend(2)
        for b in main:
            self.bc.add_block(b)
        rival_miner = type(self.miner)("rival", 1)
        a1 = mine_on(self.genesis, rival_miner, (), self.p)
        self.assertEqual(self.bc.add_block(a1).outcome, Outcome.SIDE_BRANCH)
        self.assertEqual(len(self.bc.side_tips()), 1)
        a2 = mine_on(a1, rival_miner, (), self.p)
        self.assertEqual(self.bc.add_block(a2).outcome, Outcome.SIDE_BRANCH)  # equal length: keep first seen
        self.assertEqual(self.bc.tip, main[-1].hash)
        a3 = mine_on(a2, rival_miner, (), self.p)
        self.assertEqual(self.bc.add_block(a3).outcome, Outcome.REORG)
        self.assertEqual(self.bc.tip, a3.hash)
        self.assertEqual(self.bc.tip_state.balance(rival_miner.address), 150)
        self.assertEqual(self.bc.tip_state.balance(self.miner.address), 0)

    def test_integrity_verification_detects_tampering(self):
        for b in self.extend(3):
            self.bc.add_block(b)
        self.assertTrue(self.bc.verify_integrity()["valid"])
        victim = self.bc.chain[2]
        victim.transactions[0] = dataclasses.replace(victim.transactions[0], amount=51)
        res = self.bc.verify_integrity()
        self.assertFalse(res["valid"])
        self.assertEqual(res["bad_index"], 2)
        self.assertFalse(self.bc.verify_block(2)["valid"])
        self.assertTrue(self.bc.verify_block(1)["valid"])

    def test_difficulty_schedule_applies_from_given_height(self):
        self.p.set_difficulty(3, 12)
        self.assertEqual([self.p.difficulty_for(i) for i in (1, 2, 3, 9)], [8, 8, 12, 12])


if __name__ == "__main__":
    unittest.main()
