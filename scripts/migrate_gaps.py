#!/usr/bin/env python3
"""migrate_gaps.py — one-off cleanup of ~/.tool-router/gaps.json.

Why this exists (measured 2026-10-05): _intent_key used to join EVERY content
token of a prompt, so any large pasted prompt became its own key. The worst
entry was a 4,581-char key made of a whole Astra training-pipeline system
prompt; it fuzzy-matched unrelated traffic and its count reached 720, which meant
every subsequent route through that key paid the full network sourcing cost
(24 s) for an intent nobody was ever going to install.

What it does:
  1. Re-keys every long intent (> gaptrack.MAX_INTENT_TOKENS tokens) to the new
     stable hash form, carrying count/served/installed/candidates across.
  2. Renames the token-soup KEYS whose entry tokens number in the hundreds into
     hash keys as well.
  3. Drops entries that are pure noise: hashed or long AND never judged
     (served is None) AND an auto install never happened (installed is None).
     Their history has no decision value — the oracle never ruled on them.
  4. Caps examples per entry and rewrites the file atomically.

Safety: writes a timestamped backup next to the file, dry-run by default, and
never touches an entry that has a non-null served verdict or an installed skill.

Usage:
    python3 migrate_gaps.py            # dry run, prints a plan
    python3 migrate_gaps.py --apply    # do it
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gaptrack  # noqa: E402

KEEP_SERVED = True     # preserve anything the oracle judged
DROP_NOISE = True      # drop unjudged, never-installed, oversized entries
MAX_EXAMPLES = 3


def plan(data: dict) -> dict:
    rekeyed, dropped, trimmed = {}, [], 0
    out: dict = {}
    for key, entry in data.items():
        toks = entry.get("tokens") or str(key).split()
        ntok = len(toks)
        long_enough = ntok > gaptrack.MAX_INTENT_TOKENS or gaptrack.key_is_hashed(key)
        judged = entry.get("served") is not None
        installed = bool(entry.get("installed"))

        if long_enough and not judged and not installed and DROP_NOISE:
            dropped.append((key, ntok, entry.get("count", 0)))
            continue

        new_key = gaptrack._intent_key(" ".join(toks)) if long_enough else key
        if long_enough:
            rekeyed[key[:40]] = new_key
        entry = dict(entry)
        entry["tokens"] = sorted(set(toks))[:64]        # cap stored tokens
        ex = entry.get("examples") or []
        if len(ex) > MAX_EXAMPLES:
            entry["examples"] = ex[-MAX_EXAMPLES:]
            trimmed += 1
        # merge if the target key already exists (hash collision or rekey dup)
        if new_key in out:
            prev = out[new_key]
            prev["count"] = int(prev.get("count", 0)) + int(entry.get("count", 0))
            prev["last"] = max(int(prev.get("last", 0)), int(entry.get("last", 0)))
            for field in ("served", "installed", "candidates"):
                if entry.get(field) is not None and prev.get(field) is None:
                    prev[field] = entry[field]
        else:
            out[new_key] = entry
    return {"out": out, "rekeyed": rekeyed, "dropped": dropped, "trimmed": trimmed}


def main(argv: list[str]) -> int:
    apply = "--apply" in argv
    path = gaptrack._path()
    data = gaptrack.load()
    if not data:
        print(f"nothing to migrate ({path} empty or unreadable)")
        return 0
    before_len = sum(len(v.get("examples") or []) for v in data.values())
    p = plan(data)

    verb = "APPLYING" if apply else "DRY RUN — pass --apply to write"
    print(f"{verb}: {path}")
    print(f"  entries: {len(data)} -> {len(p['out'])}")
    print(f"  rekeyed to hash form: {len(p['rekeyed'])}")
    print(f"  dropped (unjudged + oversized + never installed): {len(p['dropped'])}")
    print(f"  examples trimmed: {p['trimmed']}")
    after_len = sum(len(v.get("examples") or []) for v in p["out"].values())
    print(f"  example prompts: {before_len} -> {after_len}")
    for k, ntok, cnt in p["dropped"][:5]:
        print(f"    drop: {ntok} tok, count={cnt}: {k[:60]}")
    if not apply:
        return 0

    bak = path.with_suffix(f".json.bak.{int(time.time())}")
    bak.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(p["out"], indent=1), encoding="utf-8")
    tmp.replace(path)
    print(f"  wrote {path} ({path.stat().st_size} bytes); backup {bak.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))