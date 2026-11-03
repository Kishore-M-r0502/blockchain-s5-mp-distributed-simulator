import dataclasses
import unittest

from simulator.mempool import Mempool
from simulator.transaction import LedgerState
from simulator.wallet import Wallet


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.alice, self.bob, self.eve = Wallet("alice", 3), Wallet("bob", 3), Wallet("eve", 3)
        self.state = LedgerState({self.alice.address: 100})

    def tx(self, amount=10, nonce=0, receiver=None):
        return self.alice.create_transaction(receiver or self.bob.address, amount, nonce, 5)

    def test_valid_signature_accepted(self):
        self.assertIsNone(self.tx().stateless_error())

    def test_modified_amount_invalidates_signature(self):
        bad = dataclasses.replace(self.tx(), amount=99)
        self.assertEqual(bad.stateless_error(), "invalid signature")

    def test_signature_from_other_key_is_rejected(self):
        t = self.tx()
        forged = dataclasses.replace(t, signature=self.eve.sign(t.signing_payload()))
        self.assertEqual(forged.stateless_error(), "invalid signature")

    def test_address_must_match_public_key(self):
        t = self.tx()
        swapped = dataclasses.replace(t, public_key=self.eve.public_key)
        self.assertIn("does not match", swapped.stateless_error())

    def test_signatures_are_deterministic_and_wallet_hides_secret(self):
        self.assertEqual(self.tx().signature, self.tx().signature)
        self.assertNotIn(str(vars(self.alice).get("_Wallet__d")), repr(self.alice))
        self.assertEqual(Wallet("alice", 3).address, self.alice.address)
        self.assertNotEqual(Wallet("alice", 4).address, self.alice.address)

    def test_ledger_enforces_balance_and_nonce(self):
        self.assertIsNone(self.state.apply(self.tx(60, 0)))
        self.assertIn("insufficient", self.state.apply(self.tx(60, 1)))
        self.assertIn("double spend", self.state.apply(self.tx(10, 0, receiver=self.eve.address)))
        self.assertIn("nonce gap", self.state.apply(self.tx(10, 5)))
        self.assertEqual(self.state.balance(self.alice.address), 40)

    def test_non_positive_and_self_payments_rejected(self):
        self.assertIsNotNone(self.alice.create_transaction(self.bob.address, 0, 0, 1).stateless_error())
        self.assertIsNotNone(self.alice.create_transaction(self.alice.address, 5, 0, 1).stateless_error())


class MempoolTests(TransactionTests):
    def test_duplicate_and_double_spend_conflict(self):
        pool, t = Mempool(), self.tx()
        self.assertEqual(pool.add(t, self.state), (True, ""))
        self.assertEqual(pool.add(t, self.state), (False, "duplicate transaction"))
        ok, reason = pool.add(self.tx(20, 0, receiver=self.eve.address), self.state)
        self.assertFalse(ok)
        self.assertIn("double spend", reason)
        self.assertEqual(len(pool), 1)
        self.assertEqual(len(pool.rejected), 1)

    def test_invalid_signature_and_overspend_rejected(self):
        pool = Mempool()
        self.assertFalse(pool.add(dataclasses.replace(self.tx(), amount=5), self.state)[0])
        self.assertIn("insufficient", pool.add(self.tx(500), self.state)[1])

    def test_select_respects_nonce_order_and_balance(self):
        pool = Mempool()
        t1, t2, t3 = self.tx(60, 0), self.tx(30, 1), self.tx(30, 2)
        for t in (t3, t2, t1):  # arrives out of order
            pool.add(t, self.state)
        chosen = pool.select_for_block(self.state, 10)
        self.assertEqual([t.nonce for t in chosen], [0, 1])  # third would overspend
        self.assertEqual(len(pool.select_for_block(self.state, 1)), 1)

    def test_confirmed_transactions_leave_pool_and_rivals_are_evicted(self):
        pool, t, rival_state = Mempool(), self.tx(), self.state.copy()
        pool.add(t, self.state)
        rival = self.tx(20, 0, receiver=self.eve.address)
        rival_state.apply(rival)  # chain confirmed a *different* tx with the same nonce
        dropped = pool.revalidate(rival_state)
        self.assertEqual([d[0].id for d in dropped], [t.id])
        self.assertEqual(len(pool), 0)


if __name__ == "__main__":
    unittest.main()
