"""REST API. Every handler is a thin wrapper around the Simulation engine."""
from __future__ import annotations

import functools

from flask import Blueprint, Response, current_app, jsonify, request

from simulator.persistence import load_simulation, save_simulation
from simulator.report import render_pdf
from simulator.scenarios import scenario_list
from simulator.simulation import INVALID_BLOCK_KINDS, SimulationError

api = Blueprint("api", __name__)


def sim():
    return current_app.config["SIM"]


def handler(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except SimulationError as exc:
            return jsonify(error=str(exc)), 400
        except (KeyError, TypeError, ValueError) as exc:
            return jsonify(error=f"invalid request: {exc}"), 400

    return wrapper


def body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def need(data: dict, *keys):
    missing = [k for k in keys if k not in data]
    if missing:
        raise SimulationError(f"missing field(s): {', '.join(missing)}")
    return [data[k] for k in keys]


@api.get("/status")
@handler
def status():
    return jsonify(sim().snapshot())


@api.get("/nodes")
@handler
def nodes():
    return jsonify(sim().snapshot()["nodes"])


@api.post("/nodes")
@handler
def create_node():
    nid = sim().add_node(body().get("hashrate"))
    return jsonify(id=nid), 201


@api.get("/nodes/<nid>")
@handler
def node_detail(nid):
    return jsonify(sim().node_detail(nid))


@api.delete("/nodes/<nid>")
@handler
def delete_node(nid):
    sim().remove_node(nid)
    return jsonify(ok=True)


@api.post("/nodes/<nid>/status")
@handler
def node_status(nid):
    (online,) = need(body(), "online")
    sim().set_node_online(nid, bool(online))
    return jsonify(ok=True)


@api.post("/nodes/<nid>/hashrate")
@handler
def node_hashrate(nid):
    (rate,) = need(body(), "hashrate")
    sim().set_hashrate(nid, rate)
    return jsonify(ok=True)


@api.get("/nodes/<nid>/chain")
@handler
def node_chain(nid):
    return jsonify(sim().node_chain(nid))


@api.get("/nodes/<nid>/blocks/<int:index>/verify")
@handler
def verify_block(nid, index):
    try:
        return jsonify(sim().node(nid).blockchain.verify_block(index))
    except IndexError as exc:
        raise SimulationError(str(exc))


@api.get("/nodes/<nid>/validate")
@handler
def validate_node(nid):
    return jsonify(sim().node(nid).blockchain.verify_integrity())


@api.post("/nodes/<nid>/tamper")
@handler
def tamper(nid):
    (index,) = need(body(), "index")
    return jsonify(sim().tamper_block(nid, index))


@api.post("/nodes/<nid>/invalid-block")
@handler
def invalid_block(nid):
    (kind,) = need(body(), "kind")
    return jsonify(sim().inject_invalid_block(nid, kind))


@api.get("/invalid-block-kinds")
def invalid_kinds():
    return jsonify(INVALID_BLOCK_KINDS)


@api.post("/validate")
@handler
def validate_all():
    return jsonify(sim().validate_all())


@api.post("/peers/connect")
@handler
def connect():
    a, b = need(body(), "a", "b")
    sim().connect(a, b)
    return jsonify(ok=True)


@api.post("/peers/disconnect")
@handler
def disconnect():
    a, b = need(body(), "a", "b")
    sim().disconnect(a, b)
    return jsonify(ok=True)


@api.post("/network/partition")
@handler
def partition():
    data = body()
    groups = data.get("groups")
    if not groups:  # default: split the node list in two halves
        ids = sorted(sim().network.nodes, key=lambda s: (len(s), s))
        groups = [ids[: len(ids) // 2], ids[len(ids) // 2:]]
    return jsonify(links_cut=sim().partition(groups), groups=groups)


@api.post("/network/heal")
@handler
def heal():
    return jsonify(links_restored=sim().heal())


@api.post("/network/latency")
@handler
def latency():
    data = body()
    (ms,) = need(data, "latency_ms")
    sim().set_latency(int(ms), data.get("jitter_ms"))
    return jsonify(ok=True)


@api.get("/wallets")
@handler
def wallets():
    return jsonify(sim().wallets_info())  # names, addresses, balances only - never keys


@api.get("/transactions")
@handler
def transactions():
    return jsonify(sim().tx_explorer())


@api.post("/transactions")
@handler
def create_tx():
    sender, receiver, amount, node = need(body(), "sender", "receiver", "amount", "node")
    return jsonify(sim().create_transaction(sender, receiver, amount, node, body().get("nonce"))), 201


@api.post("/mining/start")
@handler
def mining_start():
    data = body()
    (node,) = need(data, "node")
    sim().start_mining(node, data.get("blocks"))
    return jsonify(ok=True)


@api.post("/mining/stop")
@handler
def mining_stop():
    (node,) = need(body(), "node")
    sim().stop_mining(node)
    return jsonify(ok=True)


@api.post("/mining/mine-one")
@handler
def mine_one():
    (node,) = need(body(), "node")
    sim().mine_one(node)
    return jsonify(ok=True)


@api.post("/mining/difficulty")
@handler
def difficulty():
    (bits,) = need(body(), "difficulty")
    return jsonify(from_height=sim().set_difficulty(bits))


@api.get("/consensus")
def consensus():
    return jsonify(sim().consensus_info())


@api.get("/scenarios")
def scenarios():
    return jsonify(scenario_list())


@api.post("/scenarios/<name>/run")
@handler
def run_scenario(name):
    return jsonify(sim().run_scenario(name, body().get("seed")))


@api.post("/simulation/pause")
@handler
def pause():
    sim().pause()
    return jsonify(ok=True)


@api.post("/simulation/resume")
@handler
def resume():
    sim().resume()
    return jsonify(ok=True)


@api.post("/simulation/speed")
@handler
def speed():
    (value,) = need(body(), "speed")
    sim().set_speed(value)
    return jsonify(ok=True)


@api.post("/reset")
@handler
def reset():
    data = body()
    sim().reset(data.get("seed"), data.get("nodes"), data.get("topology"))
    return jsonify(ok=True)


@api.get("/events")
@handler
def events():
    since = request.args.get("since", 0, type=int)
    return jsonify([e.to_dict() for e in sim().events.since(since, 500)])


@api.get("/simulation/log")
@handler
def export_log():
    return jsonify(sim().export_log())


@api.post("/simulation/save")
@handler
def save():
    (name,) = need(body(), "name")
    return jsonify(save_simulation(sim(), current_app.config["STORAGE"], name))


@api.post("/simulation/load")
@handler
def load():
    (name,) = need(body(), "name")
    return jsonify(load_simulation(sim(), current_app.config["STORAGE"], name))


@api.get("/report.pdf")
@handler
def report():
    return Response(render_pdf(sim()), mimetype="application/pdf",
                    headers={"Content-Disposition": "attachment; filename=blockchain-simulation-report.pdf"})
