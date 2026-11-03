# Distributed Blockchain Network Simulator

An educational, **deterministic** simulator of a distributed blockchain network: independent nodes, real SHA-256 Proof of Work, gossip over a simulated network with latency, forks, longest-chain reorganisation, and a live D3.js visualisation. A Flask + Socket.IO web app and an optional argparse CLI drive the *same* engine (`simulator/simulation.py`).

## Objectives

* Show how blocks, mining, gossip and consensus really work, using real hashing and real signatures.
* Make forks, partitions, invalid blocks and double spends **reproducible**: same seed, same actions, same result.
* Keep the engine small, dependency-free and testable.

## Features

* Blocks with index, timestamp, transactions, previous hash, hash, nonce, difficulty; Merkle root; genesis; full validation; chain integrity verification.
* Proof of Work (leading-zero-bit difficulty) with an asynchronous mining loop, per-node hash rate, mining competition, stale-work discarding.
* Nodes with private chain, mempool, peers, status, miner; add/remove nodes, connect/disconnect, take offline, partition/heal.
* Transactions: account model with nonces, Schnorr signatures (secp256k1, pure Python), mempool with duplicate and double-spend rejection, recovery of transactions from orphaned blocks.
* Message layer: `TRANSACTION`, `BLOCK`, `CHAIN_REQUEST`, `CHAIN_RESPONSE`, `PEER_CONNECT`, `PEER_DISCONNECT`; ids, queues, seeded latency/jitter, failed delivery, duplicate protection, event log.
* Web UI: live graph (drag nodes, click to inspect, hover links, animated message particles, node flashes), overview, blockchain explorer with per-block verification and tamper demo, fork tree, transaction explorer, mining monitor, event log, node inspector, all controls, six scenarios, PDF report.
* Optional CLI (`python -m simulator ...`), JSON persistence with replay-verified loading.

## Architecture

```
distributed-blockchain-simulator/
├── app.py                  web entry point
├── config.json             defaults (seed, nodes, difficulty, latency, hash rate, ...)
├── simulator/              engine, no web dependency
│   ├── hashing.py          sha256, Merkle root, difficulty test
│   ├── wallet.py           Schnorr keys/signatures (educational)
│   ├── transaction.py      Transaction + LedgerState (balances, nonces)
│   ├── block.py            Block
│   ├── mempool.py          pending transactions, conflicts, selection
│   ├── consensus.py        block validity rules, fork choice, reorg path, assumptions
│   ├── blockchain.py       per-node block tree + best-chain pointer
│   ├── miner.py            Proof of Work and per-node mining process
│   ├── message.py          message types
│   ├── node.py             node logic and message handlers
│   ├── network.py          links, latency, delivery, partitions
│   ├── scheduler.py        discrete-event clock (simulated ms)
│   ├── events.py           event log
│   ├── simulation.py       engine facade: commands, replay, digest, read models
│   ├── scenarios.py        the six scripted scenarios
│   ├── persistence.py      StorageBackend + JSON file backend
│   ├── report.py           dependency-free PDF report
│   └── cli.py              argparse CLI
├── web/                    Flask app factory, REST API, Socket.IO driver
├── templates/index.html
├── static/{css,js,vendor}  UI (D3 and Socket.IO client vendored for offline use)
├── data/                   saved simulations
└── tests/                  unittest suite (76 tests)
```

Dependency direction: `web` and `cli` → `Simulation` → `Network`/`Node` → `Blockchain`/`Mempool`/`Miner` → `consensus` → `block`/`transaction`/`wallet`.

## Determinism and time

* **Simulation time** is an integer count of simulated milliseconds held by `Scheduler`. It advances only through `Simulation.advance(ms)`. The engine never reads the wall clock. The UI header shows simulated time.
* **Wall-clock time** only decides how fast the web driver feeds `advance()` (speed × real time). It cannot change outcomes. (The CLI `mine` command prints real seconds on purpose, labelled as such.)
* Randomness: one `random.Random` seeded from the seed (latency jitter). Wallet keys derive from `(seed, name)`. Mining nonces start at 0. Events fire in `(time, insertion order)`. Peers are always iterated sorted.
* A mining attempt costs `1000 / hashrate` simulated ms, so a solution found after *k* attempts is published `k·1000/hashrate` ms later, regardless of your CPU.
* Every state-changing call is recorded with its simulated timestamp. `export_log()` / `Simulation.from_log()` replay it and reproduce the identical **state digest** (shown in the header). Saved sessions are verified this way on load.

## Blockchain principles (as implemented)

A block header is `index | timestamp | previous_hash | merkle_root | difficulty | nonce`; the hash is SHA-256 of it. A block is valid when its hash matches its contents, links to its parent, has at least `difficulty` leading zero bits, uses the network's required difficulty for its height, has a coinbase of exactly the block reward as first transaction, and every other transaction is correctly signed, has the sender's next nonce and is covered by the sender's balance. Changing any historical field changes its hash and breaks every later link; "Verify whole chain" and the tamper demo show this.

## Network simulation

Nodes only communicate through `Network.send`. A message is delivered after `latency ± jitter` simulated ms, unless the sender/receiver is offline or the link vanished (drops are logged). Transactions and blocks are gossiped to all peers except the sender; each node de-duplicates by message id and by transaction/block id. Unknown parents trigger `CHAIN_REQUEST` / `CHAIN_RESPONSE`. Connections are **undirected** (drawn as plain lines); messages are directed (particles). Links created at runtime perform a visible `PEER_CONNECT` handshake that also triggers chain sync; the initial topology is pre-connected.

## Consensus mechanism

Longest valid chain wins (block count). On equal length a node keeps the tip it saw first. Each node stores every valid block it hears of (a block tree); when a branch becomes longer the node **reorganises**: orphaned blocks' transactions are re-validated against the new state and returned to the mempool when still valid, the mempool is purged of confirmed or superseded transactions, and the new tip is relayed.

**Simplified assumptions** (also shown in the UI): no accumulated-work comparison, no difficulty retargeting (difficulty is a height-keyed schedule changed by the user), no fees or reward halving, identical rules and genesis for all nodes. Longest chain is *not* automatically the most secure chain: a majority of hash power can rewrite history (51% attack), and the simulator does not model attacker economics.

## Fork resolution, step by step

1. A partition makes two groups mine on the same parent → two valid branches.
2. After healing, `PEER_CONNECT` shows each side a tip it does not know → `CHAIN_REQUEST` → `CHAIN_RESPONSE`.
3. The shorter side stores the other branch (`FORK_DETECTED`) until it is longer, then switches (`CHAIN_REORG`), recovering or rejecting orphaned transactions.
4. The reorganised node relays its new tip so its whole group follows.

## Installation

Requires Python 3.11+.

```bash
cd distributed-blockchain-simulator
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run the website

```bash
python app.py                        # http://127.0.0.1:5000
python app.py --port 8080 --seed 7   # options: -H host, -p port, -s seed, -c config
```

Open the page, pick a scenario and press **Run scenario**, or click a node and press **Start mining**. Use the speed selector to go faster. Reset with a given seed to replay identically.

## Use the CLI (optional)

```bash
python -m simulator --help
python -m simulator scenario --list
python -m simulator scenario recovery -v              # run a scenario, print events and checks
python -m simulator run -n 4 -s 7 -t 30 -m node-1 node-2 -o run.json -p run.pdf
python -m simulator replay run.json                   # proves the replay is identical
python -m simulator mine -b 3 -d 16                   # standalone Proof of Work demo
```

Short and long flags exist for every option (`-n/--nodes`, `-s/--seed`, `-d/--difficulty`, `-t/--duration`, `-l/--latency`, `-m/--miners`, `-x/--transactions`, `-j/--json`, `-v/--verbose`, `-o/--output`, `-p/--pdf`). Exit codes: `0` success, `1` finished but verification failed, `2` invalid arguments/request, `3` file error.

## Example walkthroughs

* **Fork and recovery:** run *Blockchain fork*, open the **Forks** tab (two branches, different tip colours), then run *Network recovery* and watch the **Event log** for `FORK_DETECTED`, `CHAIN_SYNC`, `CHAIN_REORG`.
* **Double spend:** run *Double spending*; open **Transactions → Rejected** to see both the immediate mempool conflict and the post-reorg rejection.
* **Tampering:** in **Blockchain explorer** corrupt block 1 of a node, then *Verify whole chain*.
* **Manual partition:** *Split network in two*, send transactions through nodes on each side, mine on both, then *Heal network*.

## REST and Socket.IO

REST (JSON): `GET /api/status`, `GET|POST /api/nodes`, `GET|DELETE /api/nodes/<id>`, `POST /api/nodes/<id>/status`, `GET /api/nodes/<id>/chain|validate`, `GET /api/nodes/<id>/blocks/<n>/verify`, `POST /api/peers/connect|disconnect`, `POST /api/network/partition|heal|latency`, `GET|POST /api/transactions`, `POST /api/mining/start|stop|mine-one|difficulty`, `GET /api/scenarios`, `POST /api/scenarios/<name>/run`, `POST /api/simulation/pause|resume|speed|save|load`, `POST /api/reset`, `GET /api/events`, `GET /api/simulation/log`, `GET /api/report.pdf`.
Socket.IO (server → client): `sim:snapshot`, `sim:events`, `sim:messages`, `sim:tick`, `sim:reset`.
Private keys exist only inside `Wallet` objects; no endpoint, log or event exposes them (covered by a test).

## Tests

```bash
python -m unittest discover -s tests -t .
```

76 tests cover genesis, hashing, Proof of Work, block/chain validation and tamper detection, signatures, ledger and mempool behaviour, node independence, message latency/duplication/loss, partition and recovery, forks and reorganisation, every scenario, determinism and replay, persistence, PDF, CLI and the REST/Socket.IO layer.

## Limitations

Single process; the PDF report is plain text; the UI is built for tens of nodes, not thousands; the browser UI loads D3/Socket.IO from `static/vendor` (CDN fallback). The browser pacing uses real time, so the *speed* of the UI differs between machines but never the result.

## How this differs from a real blockchain

Real networks run untrusted nodes over the internet with unpredictable timing; here time is simulated and central. Real chains compare accumulated work, retarget difficulty automatically, use fees, UTXO or richer state, hardened constant-time cryptography, peer discovery, DoS protection and years of consensus-bug fixes. Keys here are derived from a public seed, so they are **not secret** in any real sense. Do not use this code to secure value.

## Future enhancements

Accumulated-work fork choice and difficulty retargeting, fee market, selfish-mining and 51% attack scenarios, header-first sync, SQLite storage backend (implement `StorageBackend`), per-link latency, multi-chain comparison.
