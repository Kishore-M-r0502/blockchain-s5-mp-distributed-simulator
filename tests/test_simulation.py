import json
import os
import tempfile
import unittest

from simulator import cli
from simulator.persistence import JSONFileStorage, load_simulation, save_simulation
from simulator.report import render_pdf
from simulator.scenarios import SCENARIOS
from simulator.simulation import Simulation, SimulationError


def busy_run(seed):
    sim = Simulation(seed=seed, nodes=5, topology="ring+chord")
    sim.start_mining("all")
    for i in range(4):
        sim.advance(3000)
        sim.create_transaction("alice", "bob", 3 + i, f"node-{i + 1}")
    sim.advance(20000)
    return sim


class DeterminismTests(unittest.TestCase):
    def test_same_seed_gives_identical_state(self):
        a, b = busy_run(11), busy_run(11)
        self.assertEqual(a.state_digest(), b.state_digest())
        self.assertEqual([e.message for e in a.events.since(0)], [e.message for e in b.events.since(0)])
        self.assertEqual([n.blockchain.tip for n in a.network.nodes.values()], [n.blockchain.tip for n in b.network.nodes.values()])

    def test_different_seed_gives_different_state(self):
        self.assertNotEqual(busy_run(11).state_digest(), busy_run(12).state_digest())

    def test_chunking_of_time_does_not_change_the_outcome(self):
        a = Simulation(seed=3, nodes=4)
        a.start_mining("all")
        a.advance(30000)
        b = Simulation(seed=3, nodes=4)
        b.start_mining("all")
        for _ in range(300):
            b.advance(100)
        self.assertEqual(a.state_digest(), b.state_digest())

    def test_no_wall_clock_dependence(self):
        import time
        real = time.time
        try:
            time.time = lambda: 0.0
            a = busy_run(5).state_digest()
            time.time = lambda: 1e9
            b = busy_run(5).state_digest()
        finally:
            time.time = real
        self.assertEqual(a, b)

    def test_replay_from_command_log_reproduces_state(self):
        sim = busy_run(21)
        sim.set_difficulty(11)
        sim.partition([["node-1", "node-2"], ["node-3", "node-4", "node-5"]])
        sim.advance(15000)
        sim.heal()
        sim.advance(10000)
        log = json.loads(json.dumps(sim.export_log()))
        again = Simulation.from_log(log)
        self.assertEqual(again.state_digest(), sim.state_digest())
        self.assertEqual(again.now_ms, sim.now_ms)

    def test_scenario_replay_reproduces_state(self):
        sim = Simulation()
        sim.run_scenario_blocking("fork")
        again = Simulation.from_log(json.loads(json.dumps(sim.export_log())))
        self.assertEqual(again.state_digest(), sim.state_digest())

    def test_failed_commands_are_not_logged(self):
        sim = Simulation(seed=1, nodes=2)
        with self.assertRaises(SimulationError):
            sim.connect("node-1", "node-9")
        self.assertEqual(sim.export_log()["commands"], [])

    def test_all_scenarios_are_reproducible(self):
        for name in SCENARIOS:
            with self.subTest(name=name):
                a, b = Simulation(), Simulation()
                a.run_scenario_blocking(name)
                b.run_scenario_blocking(name)
                self.assertEqual(a.state_digest(), b.state_digest())


class EngineBehaviourTests(unittest.TestCase):
    def test_input_validation(self):
        sim = Simulation(seed=1, nodes=2)
        for call in (lambda: sim.set_difficulty(0), lambda: sim.set_difficulty(99), lambda: sim.set_latency(0),
                     lambda: sim.create_transaction("alice", "bob", -5, "node-1"),
                     lambda: sim.create_transaction("zed", "bob", 5, "node-1"),
                     lambda: sim.start_mining("node-9"), lambda: sim.inject_invalid_block("node-1", "nope")):
            with self.assertRaises(SimulationError):
                call()

    def test_difficulty_change_takes_effect_for_new_blocks(self):
        sim = Simulation(seed=2, nodes=2)
        sim.mine_one("node-1")
        sim.advance(30000)
        sim.set_difficulty(12)
        sim.mine_one("node-1")
        sim.advance(120000)
        chain = sim.node("node-2").blockchain.chain
        self.assertEqual([b.difficulty for b in chain[1:]], [10, 12])

    def test_private_keys_never_reach_api_payloads(self):
        sim = busy_run(4)
        blob = json.dumps([sim.snapshot(), sim.tx_explorer(), sim.wallets_info(), sim.node_chain("node-1"), sim.export_log(),
                           [e.to_dict() for e in sim.events.since(0)]])
        for w in sim.wallets.values():
            secret = format(getattr(w, "_Wallet__d"), "064x")
            self.assertNotIn(secret, blob)

    def test_time_is_simulated_integer_milliseconds(self):
        sim = Simulation(seed=1, nodes=2)
        sim.advance(1234)
        self.assertEqual(sim.now_ms, 1234)
        self.assertIsInstance(sim.now_ms, int)


class PersistenceAndReportTests(unittest.TestCase):
    def test_save_and_load_roundtrip_verifies_digest(self):
        with tempfile.TemporaryDirectory() as d:
            storage = JSONFileStorage(d)
            sim = busy_run(8)
            info = save_simulation(sim, storage, "run1")
            other = Simulation(seed=99, nodes=3)
            load_simulation(other, storage, "run1")
            self.assertEqual(other.state_digest(), sim.state_digest())
            self.assertEqual(storage.list_keys(), ["run1"])
            with self.assertRaises(SimulationError):
                storage.save("../evil", {})
            doc = storage.load("run1")
            doc["digest"] = "0" * 64
            storage.save("run1", doc)
            with self.assertRaises(SimulationError):
                load_simulation(other, storage, "run1")
            self.assertEqual(info["digest"], sim.state_digest())

    def test_pdf_report_is_valid_pdf(self):
        pdf = render_pdf(busy_run(2))
        self.assertTrue(pdf.startswith(b"%PDF-1.4") and pdf.rstrip().endswith(b"%%EOF"))
        self.assertIn(b"/Type /Catalog", pdf)
        self.assertIn(b"CONSENSUS ASSUMPTIONS", pdf)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        import contextlib
        import io
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_scenario_command_passes_and_is_reproducible(self):
        c1, o1, _ = self.run_cli("scenario", "recovery", "-j")
        c2, o2, _ = self.run_cli("scenario", "recovery", "--json")
        self.assertEqual((c1, c2), (0, 0))
        self.assertEqual(json.loads(o1)["digest"], json.loads(o2)["digest"])
        self.assertEqual(json.loads(o1)["problems"], [])

    def test_run_save_and_replay(self):
        with tempfile.TemporaryDirectory() as d:
            path, pdf = os.path.join(d, "r.json"), os.path.join(d, "r.pdf")
            code, out, _ = self.run_cli("run", "-n", "3", "-s", "4", "-t", "12", "-x", "2", "-o", path, "-p", pdf)
            self.assertEqual(code, 0)
            self.assertTrue(os.path.getsize(pdf) > 500)
            code, out, _ = self.run_cli("replay", path)
            self.assertEqual(code, 0)
            self.assertIn("IDENTICAL", out)

    def test_validation_errors_and_exit_codes(self):
        self.assertEqual(self.run_cli("run", "-d", "99")[0], 2)
        self.assertEqual(self.run_cli("run", "-n", "abc")[0], 2)
        self.assertEqual(self.run_cli("run", "-m", "node-42", "-t", "2")[0], 2)
        self.assertEqual(self.run_cli("scenario")[0], 2)
        self.assertEqual(self.run_cli("replay", "/nonexistent.json")[0], 3)
        self.assertEqual(self.run_cli("--help")[0], 0)

    def test_mine_command(self):
        code, out, _ = self.run_cli("mine", "-b", "2", "-d", "10", "-j")
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["integrity"]["valid"])


if __name__ == "__main__":
    unittest.main()
