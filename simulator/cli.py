"""Optional developer CLI. Uses exactly the same engine as the web application.

Exit codes: 0 success | 1 simulation finished but verification/checks failed |
2 invalid arguments or request | 3 file / I/O error.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from typing import List, Optional

from . import __version__
from .blockchain import Blockchain
from .consensus import ConsensusParams, make_coinbase, make_genesis
from .miner import proof_of_work
from .persistence import JSONFileStorage, simulation_document
from .report import render_pdf
from .scenarios import SCENARIOS, scenario_list
from .block import Block
from .simulation import DEFAULT_CONFIG, Simulation, SimulationError
from .wallet import Wallet

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_IO = 0, 1, 2, 3
TOPOLOGIES = ["ring", "ring+chord", "full", "line", "none"]


def _int_in(lo: int, hi: int):
    def conv(text: str) -> int:
        try:
            value = int(text)
        except ValueError:
            raise argparse.ArgumentTypeError(f"'{text}' is not a whole number")
        if not lo <= value <= hi:
            raise argparse.ArgumentTypeError(f"{value} is out of range ({lo}-{hi})")
        return value

    return conv


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m simulator", formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Distributed Blockchain Network Simulator - developer CLI.\n"
                    "Runs the same deterministic engine as the website. Same seed + same options = same result.",
        epilog="examples:\n  python -m simulator run -n 4 -s 7 -t 30 -m node-1 node-2\n"
               "  python -m simulator scenario recovery -v\n  python -m simulator mine -b 3 -d 16\n"
               "  python -m simulator scenario --list")
    p.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", required=True)

    def common(sp, with_sim_opts: bool):
        sp.add_argument("-s", "--seed", type=_int_in(0, 2 ** 31), default=DEFAULT_CONFIG["seed"], help="random seed (default: %(default)s)")
        sp.add_argument("-j", "--json", action="store_true", help="print machine-readable JSON instead of text")
        sp.add_argument("-v", "--verbose", action="store_true", help="also print the event log")
        sp.add_argument("-o", "--output", metavar="FILE", help="save the replayable simulation log to FILE (JSON)")
        sp.add_argument("-p", "--pdf", metavar="FILE", help="write a PDF report to FILE")

    r = sub.add_parser("run", help="run a free-form simulation", description="Create a network, mine, send transactions and print the result.")
    common(r, True)
    r.add_argument("-n", "--nodes", type=_int_in(1, 50), default=DEFAULT_CONFIG["initial_nodes"], help="number of nodes (default: %(default)s)")
    r.add_argument("-T", "--topology", choices=TOPOLOGIES, default="ring", help="initial peer topology (default: %(default)s)")
    r.add_argument("-d", "--difficulty", type=_int_in(1, DEFAULT_CONFIG["max_difficulty"]), default=DEFAULT_CONFIG["difficulty"], help="leading zero bits (default: %(default)s)")
    r.add_argument("-l", "--latency", type=_int_in(1, 60000), default=DEFAULT_CONFIG["latency_ms"], metavar="MS", help="network latency in simulated ms (default: %(default)s)")
    r.add_argument("-J", "--jitter", type=_int_in(0, 60000), default=DEFAULT_CONFIG["latency_jitter_ms"], metavar="MS", help="latency jitter in ms (default: %(default)s)")
    r.add_argument("-r", "--hashrate", type=_int_in(1, 200000), default=DEFAULT_CONFIG["hashrate"], help="hash attempts per simulated second per node (default: %(default)s)")
    r.add_argument("-t", "--duration", type=_int_in(1, 3600), default=30, metavar="SECONDS", help="simulated seconds to run (default: %(default)s)")
    r.add_argument("-m", "--miners", nargs="+", default=["all"], metavar="NODE", help="node ids that mine, or 'all' (default: all)")
    r.add_argument("-x", "--transactions", type=_int_in(0, 500), default=4, metavar="N", help="transactions to submit, evenly spaced (default: %(default)s)")

    sc = sub.add_parser("scenario", help="run a predefined scenario", description="Run a predefined scenario to completion and check its expected outcome.")
    common(sc, False)
    sc.add_argument("name", nargs="?", choices=list(SCENARIOS), help="scenario to run")
    sc.add_argument("-l", "--list", action="store_true", help="list available scenarios and exit")

    m = sub.add_parser("mine", help="mine a standalone chain (shows Proof of Work)", description="Mine blocks on a single chain; reports attempts and real wall-clock seconds.")
    m.add_argument("-b", "--blocks", type=_int_in(1, 1000), default=3, help="blocks to mine (default: %(default)s)")
    m.add_argument("-d", "--difficulty", type=_int_in(1, 26), default=16, help="leading zero bits (default: %(default)s)")
    m.add_argument("-s", "--seed", type=_int_in(0, 2 ** 31), default=DEFAULT_CONFIG["seed"])
    m.add_argument("-j", "--json", action="store_true", help="print JSON")

    rp = sub.add_parser("replay", help="replay a saved log and verify determinism", description="Replay a log written with --output and confirm the state digest matches.")
    rp.add_argument("file", metavar="FILE")
    rp.add_argument("-j", "--json", action="store_true")
    rp.add_argument("-p", "--pdf", metavar="FILE", help="write a PDF report of the replayed state")
    return p


# ---------------------------------------------------------------------------
def _table(rows: List[List[str]], header: List[str]) -> str:
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    return "\n".join([fmt.format(*header), fmt.format(*["-" * w for w in widths])] + [fmt.format(*map(str, r)) for r in rows])


def _summary(sim: Simulation, wall_s: float) -> dict:
    snap = sim.snapshot()
    return {"simulated_seconds": sim.now_ms / 1000, "wall_clock_seconds": round(wall_s, 3), "seed": sim.seed,
            "digest": snap["sim"]["digest"], "overview": snap["overview"], "nodes": snap["nodes"],
            "balances": {a["name"]: a["balance"] for a in snap["accounts"]},
            "integrity": sim.validate_all()}


def _print_summary(sim: Simulation, s: dict, verbose: bool) -> None:
    o = s["overview"]
    print(f"simulated time : {s['simulated_seconds']:.3f} s   (wall-clock {s['wall_clock_seconds']:.3f} s)")
    print(f"seed / digest  : {s['seed']} / {s['digest']}")
    print(f"network        : height {o['network_height']}, {o['total_blocks']} blocks, {o['competing_tips']} distinct tip(s), "
          f"{o['pending_txs']} pending tx, {o['total_messages']} messages ({o['messages_dropped']} dropped)\n")
    print(_table([[n["id"], n["status"], n["height"], n["tip"][:8], len(n["peers"]), n["mempool"], n["miner"]["blocks_mined"], n["stats"]["reorgs"]]
                  for n in s["nodes"]], ["node", "status", "height", "tip", "peers", "mempool", "mined", "reorgs"]))
    print("\nbalances       : " + ", ".join(f"{k}={v}" for k, v in s["balances"].items()))
    bad = [n for n, r in s["integrity"].items() if not r["valid"]]
    print("integrity      : " + ("all chains valid" if not bad else f"INVALID chains on {', '.join(bad)}"))
    if verbose:
        print("\nevent log:")
        for e in sim.events.since(0):
            print(f"  [{e.time_ms / 1000:8.3f}s] {e.type:<16} {e.message}")


def _write_outputs(sim: Simulation, args) -> None:
    if getattr(args, "output", None):
        with open(args.output, "w", encoding="utf-8") as fh:
            json.dump(simulation_document(sim), fh, indent=2, sort_keys=True)
        print(f"log written to {args.output}", file=sys.stderr)
    if getattr(args, "pdf", None):
        with open(args.pdf, "wb") as fh:
            fh.write(render_pdf(sim))
        print(f"PDF report written to {args.pdf}", file=sys.stderr)


def cmd_run(args) -> int:
    cfg = {"difficulty": args.difficulty, "latency_ms": args.latency, "latency_jitter_ms": min(args.jitter, args.latency),
           "hashrate": args.hashrate}
    sim = Simulation(cfg, seed=args.seed, nodes=args.nodes, topology=args.topology)
    ids = sorted(sim.network.nodes, key=lambda s: (len(s), s))
    miners = ids if args.miners == ["all"] else args.miners
    unknown = [m for m in miners if m not in ids]
    if unknown:
        raise SimulationError(f"unknown miner node(s): {', '.join(unknown)} (available: {', '.join(ids)})")
    started = time.perf_counter()
    for m in miners:
        sim.start_mining(m)
    accounts, total_ms = sim.config["accounts"], args.duration * 1000
    for i in range(args.transactions):
        target = (i + 1) * total_ms // (args.transactions + 1)
        sim.advance(target - sim.now_ms)
        res = sim.create_transaction(accounts[i % len(accounts)], accounts[(i + 1) % len(accounts)], 5 + (i * 7) % 20, ids[i % len(ids)])
        if not res["accepted"]:
            print(f"note: transaction {i + 1} rejected: {res['reason']}", file=sys.stderr)
    sim.advance(total_ms - sim.now_ms)
    sim.stop_mining("all")
    sim.advance(5000)  # let in-flight blocks arrive
    summary = _summary(sim, time.perf_counter() - started)
    _write_outputs(sim, args)
    print(json.dumps(summary, indent=2) if args.json else "", end="" if args.json else "")
    if not args.json:
        _print_summary(sim, summary, args.verbose)
    return EXIT_OK if all(r["valid"] for r in summary["integrity"].values()) else EXIT_FAILED


def cmd_scenario(args) -> int:
    if args.list or not args.name:
        if not args.list:
            raise SimulationError("a scenario name is required (use --list to see them)")
        if args.json:
            print(json.dumps(scenario_list(), indent=2))
        else:
            print(_table([[s["name"], s["title"], s["summary"]] for s in scenario_list()], ["name", "title", "description"]))
        return EXIT_OK
    sim = Simulation(seed=args.seed)
    started = time.perf_counter()
    info = sim.run_scenario_blocking(args.name, args.seed)
    summary = _summary(sim, time.perf_counter() - started)
    summary["scenario"], summary["problems"] = args.name, info["problems"]
    _write_outputs(sim, args)
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"scenario: {info['title']}\n")
        for e in sim.events.since(0):
            if e.type == "SCENARIO" or args.verbose:
                print(f"  [{e.time_ms / 1000:8.3f}s] {e.type:<16} {e.message}")
        print()
        _print_summary(sim, summary, False)
        print("\nchecks         : " + ("PASS" if not info["problems"] else "FAIL - " + "; ".join(info["problems"])))
    return EXIT_OK if not info["problems"] else EXIT_FAILED


def cmd_mine(args) -> int:
    params = ConsensusParams(difficulty_schedule=[(0, args.difficulty)])
    miner = Wallet("miner", args.seed)
    bc = Blockchain(make_genesis({miner.address: 0}), params)
    rows, now_ms, wall_total = [], 0, 0.0
    for _ in range(args.blocks):
        tip = bc.latest
        now_ms += 1000  # simulated seconds between blocks; the mining *time* below is real wall-clock
        block = Block(tip.index + 1, now_ms, [make_coinbase(miner.address, tip.index + 1, now_ms, params.block_reward)],
                      tip.hash, params.difficulty_for(tip.index + 1))
        t0 = time.perf_counter()
        attempts = proof_of_work(block)
        wall = time.perf_counter() - t0
        wall_total += wall
        res = bc.add_block(block)
        if res.outcome.value != "ADDED":
            raise SimulationError(f"own block rejected: {res.reason}")
        rows.append([block.index, block.nonce, attempts, f"{wall:.3f}", block.hash[:24]])
    integrity = bc.verify_integrity()
    if args.json:
        print(json.dumps({"blocks": [dict(zip(["index", "nonce", "attempts", "wall_seconds", "hash"], r)) for r in rows],
                          "integrity": integrity}, indent=2))
    else:
        print(f"difficulty {args.difficulty} bits (expected ~{2 ** args.difficulty} attempts per block)\n")
        print(_table(rows, ["height", "nonce", "attempts", "wall s", "hash"]))
        print(f"\ntotal wall-clock mining time: {wall_total:.3f} s (depends on this machine; the web simulator uses simulated time)")
        print("integrity: " + ("chain valid" if integrity["valid"] else f"INVALID - {integrity['error']}"))
    return EXIT_OK if integrity["valid"] else EXIT_FAILED


def cmd_replay(args) -> int:
    try:
        with open(args.file, "r", encoding="utf-8") as fh:
            log = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.file}: {exc}", file=sys.stderr)
        return EXIT_IO
    sim = Simulation.from_log(log)
    match = sim.state_digest() == log.get("digest")
    if args.pdf:
        with open(args.pdf, "wb") as fh:
            fh.write(render_pdf(sim))
    result = {"file": args.file, "replayed_commands": len(log["commands"]), "simulated_seconds": sim.now_ms / 1000,
              "saved_digest": log.get("digest"), "replayed_digest": sim.state_digest(), "identical": match}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"replayed {result['replayed_commands']} command(s) to t={result['simulated_seconds']:.3f}s")
        print(f"saved    digest: {result['saved_digest']}\nreplayed digest: {result['replayed_digest']}")
        print("deterministic replay: " + ("IDENTICAL" if match else "MISMATCH"))
    return EXIT_OK if match else EXIT_FAILED


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"run": cmd_run, "scenario": cmd_scenario, "mine": cmd_mine, "replay": cmd_replay}
    try:
        return handlers[args.command](args)
    except SimulationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_IO


if __name__ == "__main__":
    sys.exit(main())
