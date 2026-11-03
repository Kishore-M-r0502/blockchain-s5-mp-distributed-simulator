"""Plain-text simulation report rendered as a PDF with no third-party dependency."""
from __future__ import annotations

import textwrap
from typing import List

from .consensus import CONSENSUS_ASSUMPTIONS
from .simulation import Simulation

PAGE_W, PAGE_H, MARGIN, FONT, LEAD = 612, 792, 40, 8.5, 11
COLS = 100
LINES_PER_PAGE = (PAGE_H - 2 * MARGIN) // LEAD


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def lines_to_pdf(lines: List[str]) -> bytes:
    """Minimal PDF 1.4 writer: Courier text, automatic pagination."""
    wrapped: List[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, COLS, subsequent_indent="    ") or [""])
    pages = [wrapped[i:i + LINES_PER_PAGE] for i in range(0, len(wrapped), LINES_PER_PAGE)] or [[]]
    objs: List[bytes] = [b"", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>"]
    kids = []
    for i, page in enumerate(pages):
        page_id, content_id = 4 + 2 * i, 5 + 2 * i
        kids.append(f"{page_id} 0 R")
        body = [f"BT /F1 {FONT} Tf {MARGIN} {PAGE_H - MARGIN} Td {LEAD} TL"]
        body += [f"({_esc(l.encode('latin-1', 'replace').decode('latin-1'))}) '" for l in page]
        body.append("ET")
        stream = "\n".join(body).encode("latin-1", "replace")
        objs.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_W} {PAGE_H}] "
                     f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>").encode())
        objs.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objs[0] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def build_report_lines(sim: Simulation) -> List[str]:
    snap = sim.snapshot()
    s, o = snap["sim"], snap["overview"]
    L = ["DISTRIBUTED BLOCKCHAIN NETWORK SIMULATOR - REPORT", "=" * 60,
         f"Simulated time : {s['time_ms'] / 1000:.3f} s (simulation clock, not wall-clock time)",
         f"Seed           : {s['seed']}", f"State digest   : {s['digest']}",
         f"Difficulty     : {s['difficulty']} leading zero bits (~{2 ** s['difficulty']} attempts per block)",
         f"Latency        : {s['latency_ms']} ms (jitter +/-{s['jitter_ms']} ms)", ""]
    if s["scenario"]:
        L += [f"Scenario       : {s['scenario']['title']} - {s['scenario']['summary']}", ""]
    L += ["NETWORK OVERVIEW", "-" * 60]
    L += [f"{k.replace('_', ' '):<20}: {v}" for k, v in o.items()]
    L += ["", "NODES", "-" * 60,
          f"{'id':<9}{'status':<9}{'height':>6}  {'tip':<9}{'peers':>5}{'mempool':>8}{'mined':>7}  reorgs"]
    for n in snap["nodes"]:
        L.append(f"{n['id']:<9}{n['status']:<9}{n['height']:>6}  {n['tip'][:8]:<9}{len(n['peers']):>5}"
                 f"{n['mempool']:>8}{n['miner']['blocks_mined']:>7}  {n['stats']['reorgs']}")
    ref = sim.canonical_node()
    if ref:
        L += ["", f"CANONICAL CHAIN (longest chain, held by {ref.id})", "-" * 60]
        for b in ref.blockchain.chain:
            miner = sim.name_of(b.miner_address) if b.index else "genesis"
            L.append(f"#{b.index:<3} {b.hash[:16]}  nonce={b.nonce:<8} txs={max(0, len(b.transactions) - 1):<3} miner={miner}")
    L += ["", "BALANCES (canonical tip)", "-" * 60]
    L += [f"{a['name']:<8}{a['balance']:>8}" for a in snap["accounts"]]
    L += ["", "INTEGRITY CHECK (every node re-validates its whole chain)", "-" * 60]
    for nid, res in sim.validate_all().items():
        L.append(f"{nid:<9}{'valid' if res['valid'] else 'INVALID: ' + res['error']}")
    L += ["", "CONSENSUS ASSUMPTIONS", "-" * 60]
    L += [f"* {a}" for a in CONSENSUS_ASSUMPTIONS]
    L += ["", "RECENT EVENTS (simulation time)", "-" * 60]
    L += [f"[{e.time_ms / 1000:8.3f}s] {e.type:<16} {e.message}" for e in sim.events.tail(80)]
    return L


def render_pdf(sim: Simulation) -> bytes:
    return lines_to_pdf(build_report_lines(sim))
