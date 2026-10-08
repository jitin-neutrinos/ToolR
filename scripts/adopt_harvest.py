#!/usr/bin/env python3
"""adopt_harvest.py — feed the honest adoption ledger from Hermes's session DB.

The hermes-plugin routes every prompt (injecting the card) but has no
PostToolUse hook, so skill loads are invisible to gate.py. They ARE visible
in ~/.hermes/state.db as assistant tool_call messages naming skill_view.
This script (cron-friendly, off the hot path):
  1. counts hermes skill_view loads since the last run  -> adoption.record_load
  2. counts hermes routed prompts (tool-router plugin ran) similarly ->
     adoption.record_route — approximated by counting user prompts in
     gateway sessions where the plugin is wired (hermes routes on every
     non-skipped prompt; needs_skills filtering happens on the claude side
     only, so hermes routes enter the denominator conservatively — every
     hermes route counts, which biases the hermes ratio DOWN, never up).
State: ~/.tool-router/adoption-harvest.state (last-harvested timestamp).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import adoption  # noqa: E402

HOME = Path(os.path.expanduser("~/.tool-router"))
STATE = HOME / "adoption-harvest.state"
DB = Path(os.path.expanduser("~/.hermes/state.db"))


def _last() -> float:
    try:
        return float(json.loads(STATE.read_text())["last"])
    except (OSError, ValueError, KeyError):
        return time.time() - 86400


def _set_last(ts: float) -> None:
    try:
        STATE.write_text(json.dumps({"last": ts}))
    except OSError:
        pass


def run() -> dict:
    if not DB.is_file():
        return {"ok": False, "err": "no state.db"}
    last = _last()
    now = time.time()
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=3)
    try:
        loads = con.execute(
            """SELECT COUNT(*), MAX(timestamp) FROM messages
               WHERE role='assistant' AND timestamp > ? AND timestamp <= ?
               AND content LIKE '%skill_view%'""",
            (last, now)).fetchone()
        prompts = con.execute(
            """SELECT COUNT(*), MAX(timestamp) FROM messages
               WHERE role='user' AND timestamp > ? AND timestamp <= ?
               AND length(content) BETWEEN 8 AND 20000""",
            (last, now)).fetchone()
    finally:
        con.close()
    n_loads, load_ts = int(loads[0] or 0), float(loads[1] or 0)
    n_prompts, prompt_ts = int(prompts[0] or 0), float(prompts[1] or 0)
    for _ in range(n_loads):
        adoption.record_load("hermes", at=min(load_ts or now, now))
    for _ in range(n_prompts):
        adoption.record_route("hermes", f"harvest-{int(now)}", 1)
    _set_last(now)
    return {"ok": True, "loads": n_loads, "routes": n_prompts}


if __name__ == "__main__":
    print(json.dumps(run()))
