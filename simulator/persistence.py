"""Pluggable JSON persistence.

``StorageBackend`` is the only interface the rest of the code uses, so a database
backend can be added later by implementing four methods. Saved simulations contain
the deterministic command log (enough to rebuild the exact state) plus a readable
copy of every node's chain.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from abc import ABC, abstractmethod
from typing import Any, List

from .simulation import DEFAULT_CONFIG, Simulation, SimulationError, merge_config

_KEY = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class StorageBackend(ABC):
    @abstractmethod
    def save(self, key: str, data: Any) -> None: ...

    @abstractmethod
    def load(self, key: str) -> Any: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list_keys(self) -> List[str]: ...


class JSONFileStorage(StorageBackend):
    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def _path(self, key: str) -> str:
        if not _KEY.match(key):
            raise SimulationError("name may only contain letters, digits, '-' and '_' (max 64 characters)")
        return os.path.join(self.directory, f"{key}.json")

    def save(self, key: str, data: Any) -> None:
        path = self._path(key)
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
            os.replace(tmp, path)  # atomic: never leaves a half-written file
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    def load(self, key: str) -> Any:
        path = self._path(key)
        if not os.path.exists(path):
            raise SimulationError(f"no saved simulation named '{key}'")
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def exists(self, key: str) -> bool:
        return os.path.exists(self._path(key))

    def list_keys(self) -> List[str]:
        return sorted(f[:-5] for f in os.listdir(self.directory) if f.endswith(".json"))


def simulation_document(sim: Simulation) -> dict:
    doc = sim.export_log()
    doc["chains"] = {nid: [sim.block_view(b) for b in sim.network.nodes[nid].blockchain.chain]
                     for nid in sorted(sim.network.nodes)}
    return doc


def save_simulation(sim: Simulation, storage: StorageBackend, name: str) -> dict:
    doc = simulation_document(sim)
    storage.save(name, doc)
    return {"name": name, "digest": doc["digest"], "commands": len(doc["commands"]), "end_ms": doc["end_ms"]}


def load_simulation(sim: Simulation, storage: StorageBackend, name: str) -> dict:
    """Rebuild ``sim`` from a saved document and prove the replay matches the original."""
    doc = storage.load(name)
    with sim.lock:
        sim.restore(doc)
        ok = sim.state_digest() == doc.get("digest")
    if not ok:
        raise SimulationError("replay finished but the state digest differs from the saved one")
    return {"name": name, "digest": doc["digest"], "end_ms": doc["end_ms"]}


def load_config(path: str) -> dict:
    """Read ``config.json`` (missing file -> defaults). Unknown keys are rejected."""
    if not path or not os.path.exists(path):
        return dict(DEFAULT_CONFIG)
    with open(path, "r", encoding="utf-8") as fh:
        return merge_config(json.load(fh))
