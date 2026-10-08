#!/usr/bin/env python3
"""adoption.py — honest per-harness adoption ledger for the 75% floor.

Replaces the single global kept/total pair (2026-10-08 audit: it counted a
one-harness numerator against an all-harness denominator and reported a
fictional 2%). Rules of the honest ledger:

  DENOMINATOR  one route event = one entry, tagged by harness, counted ONLY
               when the router actually named skill-kind picks for a prompt
               that can load skills (needs_skills). Routing with zero skill
               picks is not a suggestion and must not dilute the ratio.
  NUMERATOR    a Skill/skill_view load observed in that harness within the
               5-minute window after the route. Sources per harness:
                 claude    gate.py --loaded (PostToolUse, live hook)
                 hermes    skill_view tool calls in ~/.hermes/state.db
                 others    absent → their ratio reads null, not 0%

Ledger file: ~/.tool-router/adoption.json — a dict of per-harness counters
plus a recent-events ring used for pairing (kept small on purpose).
Fail-open everywhere: a broken ledger must never break routing or the gate.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

HOME = Path(os.path.expanduser("~/.tool-router"))
PATH = HOME / "adoption.json"
WINDOW_S = 600          # a load within 10 min of a route pairs with it
MAX_EVENTS = 200        # ring size per harness

DEFAULTS = {
    "claude":   {"routed": 0, "loaded": 0, "events": []},
    "hermes":   {"routed": 0, "loaded": 0, "events": []},
    "opencode": {"routed": 0, "loaded": 0, "events": []},
    "agy":      {"routed": 0, "loaded": 0, "events": []},
    "codex":    {"routed": 0, "loaded": 0, "events": []},
    "gemini":   {"routed": 0, "loaded": 0, "events": []},
}


def _load() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {k: dict(v) for k, v in DEFAULTS.items()}
        for k, v in DEFAULTS.items():
            data.setdefault(k, dict(v))
            data[k].setdefault("routed", 0)
            data[k].setdefault("loaded", 0)
            data[k].setdefault("events", [])
        return data
    except (OSError, ValueError):
        return {k: dict(v) for k, v in DEFAULTS.items()}


def _save(data: dict) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(PATH)
    except OSError:
        pass


def record_route(harness: str, prompt_id: str, n_skill_picks: int) -> None:
    """Count a routed prompt IF the card named at least one skill-kind pick.
    Other capability kinds (MCP/agent/command) cannot be 'loaded' like a
    skill and stay out of the denominator (measured 2026-10-08: 78% of
    routes named zero skill picks — the old ledger counted them anyway)."""
    if n_skill_picks <= 0:
        return
    data = _load()
    h = data.setdefault(harness, {"routed": 0, "loaded": 0, "events": []})
    now = time.time()
    h["routed"] += 1
    h["events"] = [e for e in (h.get("events") or [])
                   if now - float(e.get("at", 0)) < WINDOW_S * 6][-MAX_EVENTS:]
    h["events"].append({"at": now, "pid": prompt_id, "paired": 0})
    _save(data)


def record_load(harness: str, at: float | None = None) -> None:
    """Count a skill load and pair it with the harness's most recent
    unpaired route inside the window (that route's suggestion was used)."""
    data = _load()
    h = data.setdefault(harness, {"routed": 0, "loaded": 0, "events": []})
    now = float(at if at is not None else time.time())
    paired = False
    for e in reversed(h.get("events") or []):
        if not e.get("paired") and 0 <= now - float(e.get("at", 0)) <= WINDOW_S:
            e["paired"] = 1
            paired = True
            break
    h["loaded"] += 1
    if paired:
        h["routed_paired"] = int(h.get("routed_paired", 0)) + 1
    _save(data)


def ratio(harness: str) -> float | None:
    """paired/routed for one harness; None when nothing measurable yet.
    Read as: of the routes that suggested skills, how often did this
    harness go on to load one within the window."""
    h = _load().get(harness) or {}
    routed = int(h.get("routed", 0))
    if routed < 3:
        return None            # below measurement floor: no verdict
    return int(h.get("routed_paired", 0)) / routed


def below_floor(harness: str, floor: float = 0.75) -> bool:
    r = ratio(harness)
    return r is not None and r < floor


def snapshot() -> dict:
    data = _load()
    out = {}
    for k, h in data.items():
        out[k] = {
            "routed": h.get("routed", 0),
            "loaded": h.get("loaded", 0),
            "paired": h.get("routed_paired", 0),
            "ratio": ratio(k),
        }
    return out


if __name__ == "__main__":
    print(json.dumps(snapshot(), indent=1))
