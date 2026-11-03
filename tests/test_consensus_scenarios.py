import unittest

from simulator.events import EventType
from simulator.scenarios import SCENARIOS
from simulator.simulation import Simulation


class ScenarioTests(unittest.TestCase):
    def test_every_scenario_reaches_its_expected_outcome(self):
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                sim = Simulation()
                info = sim.run_scenario_blocking(name)
                self.assertEqual(info["problems"], [])
                self.assertFalse([e for e in sim.events.since(0) if e.type == EventType.ERROR])

    def test_invalid_block_scenario_logs_a_reason_for_each_attack(self):
        sim = Simulation()
        sim.run_scenario_blocking("invalid")
        text = " ".join(e.message for e in sim.events.since(0) if e.type == EventType.BLOCK_REJECTED)
        for needle in ("proof of work", "hash does not match", "coinbase", "insufficient balance", "invalid signature"):
            self.assertIn(needle, text)

    def test_double_spend_scenario_rejects_conflict_and_reorg_victim(self):
        sim = Simulation()
        sim.run_scenario_blocking("double_spend")
        reasons = [e.message for e in sim.events.since(0) if e.type == EventType.TX_REJECTED]
        self.assertTrue(any("conflicts with pending" in r for r in reasons))
        self.assertTrue(any("nonce already confirmed" in r for r in reasons))


if __name__ == "__main__":
    unittest.main()
