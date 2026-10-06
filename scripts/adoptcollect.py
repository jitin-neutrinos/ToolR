#!/usr/bin/env python3
"""adoptcollect.py — Layer 3 data collection (24h harvest).

Collects (prompt, suggested_picks, actually_loaded) triplets from real routes
into eval/adoption.jsonl. Runs on a cron (from tomorrow); once 24h of data
exists, layer 3 uses it to nudge the card's depth + picks (the BoR-lite
prior: suggestions whose loaded-rate is high get boosted; depth converges).
Never runs on the hot path.
"""
from __future__ import annotations
import json, os, time
from pathlib import Path

HOME = Path(os.path.expanduser("~/.tool-router"))
OUT = Path("/home/notjitin/Work/tool-router/eval/adoption.jsonl")


def harvest() -> dict:
    """One harvest pass: append current observed adoption to the JSONL."""
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{os.path.expanduser('~/.hermes/state.db')}?mode=ro", uri=True, timeout=3)
        since = time.time() - 86400
        rows = con.execute("""
            SELECT m.session_id, m.role, m.content, m.timestamp
            FROM messages m WHERE m.timestamp > ? ORDER BY m.timestamp""", (since,)).fetchall()
        con.close()
    except Exception as e:
        return {"ok": False, "err": str(e)[:100]}

    # build sessions; for routed prompts, find Skill loads that followed
    out = []
    loads_at = []
    for sid, role, content, ts in rows:
        if role == "assistant" and '"name": "Skill"' in (content or ""):
            loads_at.append((sid, ts))
        if role == "user" and "[ROUTED CONTEXT" in (content or ""):
            out.append({"ts": ts, "sid": sid, "routed": 1})
    # quick join: routed prompts whose session had a Skill load within 5 min
    n_paired = 0
    for entry in out:
        paired = any(sid == entry["sid"] and 0 <= ts - entry["ts"] < 600
                     for (sid, ts) in loads_at)
        entry["loaded_after"] = 1 if paired else 0
        n_paired += int(paired)
        OUT.exists() or OUT.touch()
        with open(OUT, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    return {"ok": True, "appended": len(out), "paired": n_paired}


if __name__ == "__main__":
    print(json.dumps(harvest()))
