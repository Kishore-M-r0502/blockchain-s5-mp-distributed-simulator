import unittest

from simulator import consensus
from simulator.block import Block
from simulator.blockchain import Blockchain
from simulator.wallet import Wallet
from tests.helpers import mine_on, params, setup


class ConsensusTests(unittest.TestCase):
    def test_longest_chain_rule_and_tie_break(self):
        _, _, m, g = setup()
        p = params()
        a1 = mine_on(g, m, (), p)
        a2 = mine_on(a1, m, (), p)
        b1 = mine_on(g, Wallet("other", 1), (), p)
        self.assertTrue(consensus.is_better(a2, a1))
        self.assertFalse(consensus.is_better(b1, a1))  # equal length never displaces
        self.assertFalse(consensus.is_better(a1, a2))

    def test_reorg_path_finds_fork_point_orphans_and_adopted(self):
        _, _, m, g = setup()
        p = params()
        bc = Blockchain(Block.from_dict(g.to_dict()), p)
        a1 = mine_on(g, m, (), p)
        a2 = mine_on(a1, m, (), p)
        o = Wallet("o", 1)
        b2 = mine_on(a1, o, (), p)
        b3 = mine_on(b2, o, (), p)
        for b in (a1, a2, b2, b3):
            bc.add_block(b)
        fork, orphaned, adopted = consensus.reorg_path(bc.blocks, a2.hash, b3.hash)
        self.assertEqual(fork.hash, a1.hash)
        self.assertEqual([b.hash for b in orphaned], [a2.hash])
        self.assertEqual([b.hash for b in adopted], [b2.hash, b3.hash])

    def test_validate_chain_rejects_broken_linkage(self):
        _, _, m, g = setup()
        p = params()
        a1 = mine_on(g, m, (), p)
        a2 = mine_on(a1, m, (), p)
        self.assertTrue(consensus.validate_chain([g, a1, a2], p)[0])
        ok, err, idx = consensus.validate_chain([g, a2], p)
        self.assertFalse(ok)
        self.assertEqual(idx, 2)

    def test_assumptions_are_documented(self):
        self.assertTrue(any("longest" in a.lower() for a in consensus.CONSENSUS_ASSUMPTIONS))


if __name__ == "__main__":
    unittest.main()
