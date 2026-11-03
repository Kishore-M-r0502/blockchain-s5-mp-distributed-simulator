import unittest

from simulator.block import Block
from simulator.consensus import make_genesis
from simulator.hashing import ZERO_HASH, meets_difficulty, merkle_root, sha256_hex
from simulator.miner import proof_of_work, search_nonce
from tests.helpers import mine_on, setup


class BlockTests(unittest.TestCase):
    def test_genesis_is_deterministic_and_well_formed(self):
        a, b, _, g = setup()
        again = make_genesis({a.address: 100, b.address: 100})
        self.assertEqual(g.hash, again.hash)
        self.assertEqual((g.index, g.previous_hash, g.timestamp_ms), (0, ZERO_HASH, 0))
        self.assertTrue(g.has_valid_hash())
        self.assertNotEqual(g.hash, make_genesis({a.address: 101}).hash)

    def test_hash_matches_sha256_of_header_and_nonce(self):
        _, _, m, g = setup()
        blk = mine_on(g, m)
        self.assertEqual(blk.hash, sha256_hex(blk.header_prefix() + str(blk.nonce)))

    def test_hash_changes_when_any_field_changes(self):
        _, _, m, g = setup()
        blk = mine_on(g, m)
        original = blk.compute_hash()
        blk.nonce += 1
        self.assertNotEqual(blk.compute_hash(), original)
        blk.nonce -= 1
        blk.timestamp_ms += 1
        self.assertNotEqual(blk.compute_hash(), original)

    def test_proof_of_work_finds_real_solution(self):
        _, _, m, g = setup()
        blk = mine_on(g, m)
        self.assertTrue(int(blk.hash, 16) >> (256 - blk.difficulty) == 0)
        self.assertTrue(blk.has_valid_pow())

    def test_search_nonce_reports_attempts_and_is_reproducible(self):
        prefix = "hello|"
        n1, h1, used1 = search_nonce(prefix, 10, 0, 100000)
        n2, h2, used2 = search_nonce(prefix, 10, 0, 100000)
        self.assertEqual((n1, h1, used1), (n2, h2, used2))
        self.assertEqual(used1, n1 + 1)
        self.assertTrue(meets_difficulty(h1, 10))
        self.assertEqual(search_nonce(prefix, 40, 0, 50)[0], None)

    def test_tampering_breaks_hash_validity(self):
        _, _, m, g = setup()
        blk = mine_on(g, m)
        self.assertTrue(blk.has_valid_hash())
        blk.transactions[0] = type(blk.transactions[0])(*(blk.transactions[0].sender, blk.transactions[0].receiver, 999,
                                                         blk.transactions[0].nonce, blk.transactions[0].timestamp_ms))
        self.assertFalse(blk.has_valid_hash())

    def test_serialisation_round_trip_creates_independent_copy(self):
        _, _, m, g = setup()
        blk = mine_on(g, m)
        copy = Block.from_dict(blk.to_dict())
        self.assertEqual(copy.hash, blk.hash)
        self.assertTrue(copy.has_valid_hash())
        copy.nonce += 1
        self.assertNotEqual(copy.nonce, blk.nonce)

    def test_merkle_root_depends_on_order_and_content(self):
        self.assertNotEqual(merkle_root(["a", "b"]), merkle_root(["b", "a"]))
        self.assertNotEqual(merkle_root(["a"]), merkle_root(["b"]))
        self.assertEqual(len(merkle_root(["a", "b", "c"])), 64)


if __name__ == "__main__":
    unittest.main()
