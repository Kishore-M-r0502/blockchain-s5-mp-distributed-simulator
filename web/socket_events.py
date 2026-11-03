"""Socket.IO: a background driver advances simulated time and pushes updates.

Wall-clock time only decides *how fast* simulated time advances (speed x elapsed
real time); it never influences the simulation's outcome.

Server -> client events
  sim:snapshot  full state (overview, nodes, links, fork view, accounts, sim clock)
  sim:events    new event-log entries
  sim:messages  messages sent on the simulated network (animated as particles)
  sim:tick      lightweight clock update
  sim:reset     the simulation was reset / replaced: clear client-side logs
"""
from __future__ import annotations

TICK_WALL_S = 0.1
SNAPSHOT_MIN_S = 0.2
SNAPSHOT_IDLE_S = 1.0


def register_socket_events(socketio, sim, start_driver: bool = True) -> None:
    @socketio.on("connect")
    def on_connect():
        with sim.lock:
            socketio.emit("sim:snapshot", sim.snapshot(), to=_sid())
            socketio.emit("sim:events", {"initial": True, "events": [e.to_dict() for e in sim.events.tail(300)]}, to=_sid())

    @socketio.on("client:snapshot")
    def on_snapshot():
        socketio.emit("sim:snapshot", sim.snapshot(), to=_sid())

    def driver():
        cursor, generation, since_snapshot = sim.events.last_id, sim.generation, 0.0
        while True:
            socketio.sleep(TICK_WALL_S)
            since_snapshot += TICK_WALL_S
            try:
                with sim.lock:
                    if sim.running:
                        sim.advance(int(TICK_WALL_S * 1000 * sim.speed))
                    reset = sim.generation != generation
                    if reset:
                        generation, cursor = sim.generation, sim.reset_event_id - 1
                    events = sim.events.since(cursor, 300)
                    if events:
                        cursor = events[-1].id
                    messages = sim.network.drain_feed()
                    want_snapshot = bool(events or messages or reset) and since_snapshot >= SNAPSHOT_MIN_S
                    want_snapshot = want_snapshot or reset or since_snapshot >= SNAPSHOT_IDLE_S
                    snapshot = sim.snapshot() if want_snapshot else None
                    tick = {"time_ms": sim.now_ms, "running": sim.running, "speed": sim.speed}
                if reset:
                    socketio.emit("sim:reset", {"generation": generation})
                if events:
                    socketio.emit("sim:events", {"initial": False, "events": [e.to_dict() for e in events]})
                if messages:
                    socketio.emit("sim:messages", messages)
                if snapshot:
                    socketio.emit("sim:snapshot", snapshot)
                    since_snapshot = 0.0
                socketio.emit("sim:tick", tick)
            except Exception as exc:  # never let the driver die
                print(f"[driver] error: {type(exc).__name__}: {exc}")

    if start_driver:
        socketio.start_background_task(driver)


def _sid():
    from flask import request

    return request.sid
