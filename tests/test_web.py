import unittest

from web import create_app


class WebApiTests(unittest.TestCase):
    def setUp(self):
        self.app, self.sio = create_app(start_driver=False, seed=7)
        self.c = self.app.test_client()
        self.sim = self.app.config["SIM"]

    def test_status_nodes_and_error_contract(self):
        self.assertEqual(self.c.get("/api/status").get_json()["overview"]["total_nodes"], 5)
        self.assertEqual(len(self.c.get("/api/nodes").get_json()), 5)
        r = self.c.post("/api/mining/difficulty", json={"difficulty": 99})
        self.assertEqual(r.status_code, 400)
        self.assertIn("error", r.get_json())
        self.assertEqual(self.c.post("/api/peers/connect", json={"a": "node-1"}).status_code, 400)
        self.assertEqual(self.c.get("/api/nodes/nope").status_code, 400)

    def test_end_to_end_transaction_and_mining_through_the_api(self):
        r = self.c.post("/api/transactions", json={"sender": "alice", "receiver": "bob", "amount": 10, "node": "node-1"})
        self.assertTrue(r.get_json()["accepted"])
        self.sim.advance(3000)  # let the transaction gossip reach node-2 first
        self.c.post("/api/mining/mine-one", json={"node": "node-2"})
        self.sim.advance(40000)
        tx = self.c.get("/api/transactions").get_json()
        self.assertEqual(len(tx["confirmed"]), 1)
        self.assertEqual(tx["pending"], [])
        chain = self.c.get("/api/nodes/node-5/chain").get_json()
        self.assertEqual(chain["height"], 1)
        self.assertTrue(self.c.get("/api/nodes/node-5/blocks/1/verify").get_json()["valid"])
        self.assertEqual({w["name"]: w["balance"] for w in self.c.get("/api/wallets").get_json()}["bob"], 1010)

    def test_node_create_delete_partition_scenario_reset(self):
        self.assertEqual(self.c.post("/api/nodes", json={}).status_code, 201)
        self.assertEqual(len(self.sim.network.nodes), 6)
        self.assertEqual(self.c.delete("/api/nodes/node-6").status_code, 200)
        self.assertGreater(self.c.post("/api/network/partition", json={}).get_json()["links_cut"], 0)
        self.assertEqual(self.c.post("/api/network/heal", json={}).status_code, 200)
        self.assertEqual(self.c.post("/api/scenarios/normal/run", json={"seed": 1}).status_code, 200)
        self.assertEqual(self.c.post("/api/scenarios/nope/run", json={}).status_code, 400)
        self.assertEqual(self.c.post("/api/reset", json={"seed": 3, "nodes": 4}).status_code, 200)
        self.assertEqual(len(self.sim.network.nodes), 4)
        self.assertEqual(self.c.get("/api/report.pdf").mimetype, "application/pdf")

    def test_socket_connect_delivers_snapshot_and_events(self):
        client = self.sio.test_client(self.app)
        names = [m["name"] for m in client.get_received()]
        self.assertIn("sim:snapshot", names)
        self.assertIn("sim:events", names)

    def test_index_page_renders(self):
        html = self.c.get("/").get_data(as_text=True)
        self.assertIn("Distributed Blockchain", html)
        self.assertEqual(self.c.get("/static/js/network.js").status_code, 200)


if __name__ == "__main__":
    unittest.main()
