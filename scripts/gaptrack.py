#!/usr/bin/env python3
"""gaptrack.py — repeat-gap tracking for the auto-install tier.

Records routing gaps (zero local picks) keyed by intent, and promotes an intent
to "auto-install eligible" after it repeats >= auto_threshold times. State lives
in ~/.tool-router/gaps.json. Everything is file-based so one-shot hook processes
and interactive runs share the same truth (the breaker-file lesson, applied to
gaps).

Config (config.json -> "sourcing"):
    auto_threshold: 3     # repeat gaps before a skill auto-install is allowed
    auto_skills_only: true  # skills only; MCPs/plugins/commands always HITL
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import router_core as rc

STATE = None  # lazily: index_path().parent / "gaps.json"

# Above this many distinct content tokens an intent is keyed by hash, not by its
# literal token set (see _intent_key). 24 covers every real user request.
MAX_INTENT_TOKENS = 24


def _path() -> Path:
    global STATE
    if STATE is None:
        STATE = rc.index_path().parent / "gaps.json"
    return STATE


def _tokens(prompt: str) -> set[str]:
    """Content tokens (no truncation) — fuzzy intent matching."""
    import re
    stop = {"the", "a", "an", "and", "or", "to", "for", "of", "in", "on", "how",
            "do", "i", "my", "me", "please", "can", "you", "is", "it", "this",
            "that", "with", "using", "use", "help", "need", "want", "get", "set",
            "out", "up", "down", "off", "from", "into", "onto", "about", "after",
            "before", "over", "under", "again", "then", "when", "what", "which",
            "there", "here", "some", "any", "all", "be", "am", "are", "was"}
    return {w for w in re.findall(r"[a-z][a-z0-9_-]{2,}", (prompt or "").lower())
            if w not in stop}


def _find_key(data: dict, toks: set[str], min_jaccard: float = 0.6) -> str | None:
    """Existing intent key whose tokens overlap >= 60% (Jaccard), else None.
    Exact token-set keys are brittle ('payments' displacing one token split an
    intent into two and the oracle never saw the repeat — found live)."""
    best_key, best = None, 0.0
    for k, v in data.items():
        vt = set(v.get("tokens") or str(k).split())
        if not vt or not toks:
            continue
        j = len(vt & toks) / len(vt | toks)
        if j > best:
            best_key, best = k, j
    return best_key if best >= min_jaccard else None


def _intent_key(prompt: str) -> str:
    """Key for a NEW intent. Short intents key on their token set (readable);
    long ones (a dumped system prompt, a whole session transcript) key on a
    stable hash instead.

    Measured 2026-10-05: unbounded token joins produced a 4,581-char key from a
    single Astra training-pipeline system prompt, which fuzzy-matched ~nothing
    useful and then paid the full network sourcing cost on EVERY subsequent
    route (that entry's count reached 720). Hashing long intents caps key size
    and stops cross-intent collisions.
    """
    toks = sorted(_tokens(prompt))
    if len(toks) <= MAX_INTENT_TOKENS:
        return " ".join(toks)
    h = hashlib.sha1(" ".join(toks).encode("utf-8")).hexdigest()[:16]
    return f"intent:{h} ({len(toks)} tokens)"


def key_is_hashed(key: str) -> bool:
    return str(key).startswith("intent:")


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict) -> None:
    try:
        _path().parent.mkdir(parents=True, exist_ok=True)
        _path().write_text(json.dumps(data, indent=1), encoding="utf-8")
    except OSError:
        pass


def record_gap(prompt: str, cfg: dict) -> dict:
    """Record a route's intent (any route), fuzzy-matched to prior intents.
    Oracle runs at the 2nd sighting (source.auto_install); install needs
    oracle-confirmed unserved + threshold."""
    data = load()
    toks = _tokens(prompt)
    if not toks:
        return {}
    key = _find_key(data, toks) or _intent_key(prompt)
    entry = data.get(key) or {"first": int(time.time()), "installed": None,
                              "served": None, "examples": []}
    entry["tokens"] = sorted(toks)
    entry["last"] = int(time.time())
    entry["count"] = int(entry.get("count", 0)) + 1
    ex = entry.get("examples") or []
    if prompt[:120] not in ex:
        ex.append(prompt[:120])
        entry["examples"] = ex[-5:]
    data[key] = entry
    # prune: nothing touched in 30 days
    cutoff = time.time() - 30 * 86400
    data = {k: v for k, v in data.items() if float(v.get("last", 0)) >= cutoff}
    _save(data)
    return entry


def mark_installed(key: str, what: str) -> None:
    data = load()
    if key not in data:
        # An install marker can arrive before any route recorded the intent
        # (route --finder-install searches don't record gaps; measured
        # 2026-10-08: the marker silently no-op'd and verify then anchored to
        # the wrong intent). Create the entry instead of dropping the marker.
        now = int(time.time())
        data[key] = {"first": now, "last": now, "count": 0,
                     "tokens": key.split(), "examples": []}
    data[key]["installed"] = what
    data[key]["promoted"] = False
    _save(data)


def unmark_installed(key: str, reason: str = "manual") -> bool:
    """Clear the installed marker (an install was undone / rolled back).

    Without this a rolled-back install keeps the intent locked out forever:
    auto_install returns early on entry['installed'], so a bad candidate that was
    removed would otherwise block the correct one indefinitely. Measured
    2026-10-05: dbs-chatroom was installed by a relevance-blind auto tier and
    removed minutes later; the marker survived and locked the intent.
    """
    data = load()
    entry = data.get(key)
    if not entry or not entry.get("installed"):
        return False
    entry["installed"] = None
    entry["reopened_at"] = int(time.time())
    _save(data)
    return True


def auto_eligible(entry: dict, cfg: dict) -> bool:
    """True when the sourcing tier allows an AUTO install for this gap.

    Requires: oracle-confirmed gap (served is True), not already installed,
    and enough sightings (auto_threshold). 'served: None' = unjudged or
    oracle unsure — never auto-install. 'served: False' = a local pick serves.
    """
    s = (cfg or {}).get("sourcing") or {}
    if not s.get("auto_skills_only", True):
        return False
    if entry.get("installed"):
        return False
    if entry.get("served") is not True:
        return False
    return int(entry.get("count", 0)) >= int(s.get("auto_threshold", 3))


def resolve_key(prompt: str) -> str | None:
    """The state-file key for this prompt: fuzzy match, else its own key."""
    data = load()
    toks = _tokens(prompt)
    if not toks:
        return None
    return _find_key(data, toks) or _intent_key(prompt)


def eligible_key_for_prompt(prompt: str, cfg: dict) -> str | None:
    """The intent key for this prompt, if it has reached auto-install threshold."""
    data = load()
    key = resolve_key(prompt)
    if key:
        entry = data.get(key)
        if entry and auto_eligible(entry, cfg):
            return key
    return None
