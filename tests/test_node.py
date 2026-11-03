import unittest

from simulator.simulation import Simulation


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation(seed=5, nodes=3, topology="full")

    def test_nodes_hold_independent_state(self):
        n1, n2 = self.sim.node("node-1"), self.sim.node("node-2")
        self.assertIsNot(n1.blockchain, n2.blockchain)
        self.assertIsNot(n1.mempool, n2.mempool)
        self.assertIsNot(n1.blockchain.genesis, n2.blockchain.genesis)
        self.assertEqual(n1.blockchain.genesis.hash, n2.blockchain.genesis.hash)
        n1.blockchain.genesis.nonce = 12345  # mutate one copy only
        self.assertEqual(n2.blockchain.genesis.nonce, 0)

    def test_isolated_node_mines_alone_others_unaffected(self):
        for other in ("node-2", "node-3"):
            self.sim.disconnect("node-1", other)
        self.sim.mine_one("node-1")
        self.sim.advance(30000)
        self.assertEqual(self.sim.node("node-1").blockchain.height, 1)
        self.assertEqual(self.sim.node("node-2").blockchain.height, 0)

    def test_transaction_enters_only_origin_mempool_until_messages_arrive(self):
        self.sim.create_transaction("alice", "bob", 5, "node-1")
        self.assertEqual([len(self.sim.node(n).mempool) for n in ("node-1", "node-2", "node-3")], [1, 0, 0])
        self.sim.advance(2000)
        self.assertEqual([len(self.sim.node(n).mempool) for n in ("node-1", "node-2", "node-3")], [1, 1, 1])

    def test_confirmed_transaction_is_removed_from_every_mempool(self):
        self.sim.create_transaction("alice", "bob", 5, "node-1")
        self.sim.advance(2000)
        self.sim.mine_one("node-2")
        self.sim.advance(30000)
        self.assertEqual([len(self.sim.node(n).mempool) for n in ("node-1", "node-2", "node-3")], [0, 0, 0])
        self.assertEqual(self.sim.node("node-3").blockchain.tip_state.balance(self.sim.wallets["bob"].address), 1005)

    def test_mining_target_stops_after_requested_blocks_and_stale_work_is_discarded(self):
        self.sim.start_mining("node-1", 2)
        self.sim.advance(60000)
        m = self.sim.node("node-1").miner
        self.assertEqual((m.blocks_mined, m.active), (2, False))
        self.assertGreater(m.total_attempts, 0)

    def test_offline_node_misses_blocks_and_catches_up(self):
        self.sim.set_node_online("node-3", False)
        self.sim.mine_one("node-1")
        self.sim.advance(20000)
        self.assertEqual(self.sim.node("node-3").blockchain.height, 0)
        self.assertGreater(self.sim.network.stats["dropped"], 0)
        self.sim.set_node_online("node-3", True)
        self.sim.advance(10000)
        self.assertEqual(self.sim.node("node-3").blockchain.height, 1)
        self.assertEqual(self.sim.node("node-3").blockchain.tip, self.sim.node("node-1").blockchain.tip)


if __name__ == "__main__":
    unittest.main()
