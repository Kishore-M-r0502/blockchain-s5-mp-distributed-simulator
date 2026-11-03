import unittest

from simulator.events import EventType
from simulator.message import MessageType
from simulator.simulation import Simulation, SimulationError


class NetworkTests(unittest.TestCase):
    def setUp(self):
        self.sim = Simulation({"latency_ms": 500, "latency_jitter_ms": 0}, seed=1, nodes=4, topology="line")
        self.net = self.sim.network

    def test_message_is_delayed_by_configured_latency(self):
        msg = self.net.send("node-1", "node-2", MessageType.CHAIN_REQUEST, {"height": 0})
        self.assertEqual(msg.deliver_at_ms - msg.timestamp_ms, 500)
        self.sim.advance(499)
        self.assertEqual(self.net.stats["delivered"], 0)
        self.sim.advance(1)
        self.assertEqual(self.net.stats["delivered"], 1)

    def test_transaction_propagates_hop_by_hop_through_messages(self):
        self.sim.create_transaction("alice", "bob", 5, "node-1")
        self.sim.advance(600)
        self.assertEqual([len(self.sim.node(f"node-{i}").mempool) for i in (1, 2, 3, 4)], [1, 1, 0, 0])
        self.sim.advance(1000)
        self.assertEqual([len(self.sim.node(f"node-{i}").mempool) for i in (1, 2, 3, 4)], [1, 1, 1, 1])

    def test_duplicate_messages_and_gossip_are_ignored(self):
        self.sim.create_transaction("alice", "bob", 5, "node-1")
        tx_payload = None
        self.sim.advance(5000)
        sent_before = self.sim.node("node-2").stats["duplicates"]
        for _ in range(2):
            self.net.send("node-1", "node-2", MessageType.TRANSACTION,
                          {"tx": next(iter(self.sim.node("node-1").mempool.all())).to_dict()})
        self.sim.advance(1000)
        self.assertEqual(len(self.sim.node("node-2").mempool), 1)
        self.assertGreaterEqual(self.sim.node("node-2").stats["duplicates"], sent_before + 2)
        node = self.sim.node("node-2")
        msg = self.net.send("node-1", "node-2", MessageType.CHAIN_REQUEST, {"height": 0})
        self.sim.advance(600)
        received = node.stats["received"]
        node.enqueue(msg)  # same message id delivered again
        self.assertEqual(node.stats["received"], received)

    def test_delivery_fails_without_connection(self):
        msg = self.net.send("node-1", "node-3", MessageType.CHAIN_REQUEST, {})
        self.assertEqual((msg.status, self.net.stats["dropped"]), ("dropped", 1))
        self.assertTrue(any(e.type == EventType.MESSAGE_DROPPED for e in self.sim.events.since(0)))

    def test_link_cut_while_message_in_flight_drops_it(self):
        self.net.send("node-1", "node-2", MessageType.CHAIN_REQUEST, {})
        self.sim.disconnect("node-1", "node-2")
        self.sim.advance(2000)
        reasons = [e.data.get("reason", "") for e in self.sim.events.since(0) if e.type == EventType.MESSAGE_DROPPED]
        self.assertTrue(any("in transit" in r for r in reasons))

    def test_handshake_builds_peer_relationship(self):
        self.sim.connect("node-1", "node-4")
        self.assertNotIn("node-4", self.sim.node("node-1").peers)
        self.sim.advance(1500)
        self.assertIn("node-4", self.sim.node("node-1").peers)
        self.assertIn("node-1", self.sim.node("node-4").peers)
        with self.assertRaises(SimulationError):
            self.sim.connect("node-1", "node-4")

    def test_partition_and_recovery_synchronise_chains(self):
        sim = Simulation({"latency_ms": 200, "latency_jitter_ms": 0}, seed=9, nodes=4, topology="full")
        sim.partition([["node-1", "node-2"], ["node-3", "node-4"]])
        sim.advance(1000)
        sim.start_mining("node-1", 1)
        sim.start_mining("node-3", 2)
        sim.advance(60000)
        n = sim.network.nodes
        self.assertEqual((n["node-2"].blockchain.height, n["node-4"].blockchain.height), (1, 2))
        self.assertNotEqual(n["node-2"].blockchain.tip, n["node-4"].blockchain.tip)
        self.assertEqual(sim.fork_view()["forks"], 1)
        sim.heal()
        sim.advance(10000)
        self.assertEqual(len({x.blockchain.tip for x in n.values()}), 1)
        self.assertEqual(n["node-1"].blockchain.height, 2)
        self.assertGreaterEqual(n["node-1"].stats["reorgs"], 1)
        self.assertTrue(all(r["valid"] for r in sim.validate_all().values()))

    def test_remove_node_closes_links(self):
        self.sim.remove_node("node-2")
        self.assertNotIn("node-2", self.net.nodes)
        self.assertEqual(self.net.neighbours("node-1"), [])


if __name__ == "__main__":
    unittest.main()
