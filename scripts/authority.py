#!/usr/bin/env python3
"""authority.py — ToolR "am I the tool-decider?" check + self-install.

Contract per the owner requirement: on every route, ToolR first verifies the
harness's always-on docs (CLAUDE.md / AGENTS.md / GEMINI.md / SOUL.md —
whatever exists) already delegate tool-selection authority to ToolR. If yes →
route normally. If no → attempt to add the delegation block (the same marked
block install.py writes); if that fails (read-only fs, permissions), say so on
the card and continue unaided.

Fast path is a single substring scan over at most 4 small files, memoised
per-process: the hook is a fresh process every turn, so the memo only saves
repeated checks inside one route, not across turns — the scan itself is the
cost (measured <1 ms).

Delegation marker: the install.py mandate block already contains
"tool-router" and is written with BEGIN/END marks; authority is YES iff any
checked file contains the shared marker AND the phrase "Route before you
work" (so a stale partial block doesn't count).

Never blocks: a failed write is a one-line notice on the card, not an error.
"""
from __future__ import annotations

import os
from pathlib import Path

MARK_PHRASE = "Route before you work"   # from install.py MANDATE
# Checked in this order for the MAIN harness only — the harness whose config
# dir matched install.py's probe. We don't know which harness invoked the hook
# (no harness exports that), so we check every file that exists. The scan is
# a substring test over ≤4 small files; cost is dominated by disk, one stat.
CANDIDATE_FILES = (
    "~/.claude/CLAUDE.md",
    "~/.codex/AGENTS.md",
    "~/.gemini/GEMINI.md",
    "~/.gemini/config/GEMINI.md",
    "~/.config/opencode/AGENTS.md",
    "~/.openclaw/AGENTS.md",
    "~/AGENTS.md",          # shared location many harnesses read
    "~/.hermes/SOUL.md",    # Hermes-only, if present
)

memo: dict = {}


def _paths() -> list[Path]:
    out = []
    for p in CANDIDATE_FILES:
        if p.startswith("~"):
            out.append(Path.home() / p[2:])
        else:
            out.append(Path(p))
    return out


def is_authority() -> bool:
    for p in _paths():
        try:
            if p.is_file() and MARK_PHRASE in p.read_text(encoding="utf-8",
                                                          errors="replace"):
                memo["authority"] = True
                memo["granted_by"] = str(p)
                return True
        except OSError:
            continue
    memo["authority"] = False
    memo.setdefault("granted_by", None)
    return False


def delegated_file() -> str | None:
    return memo.get("granted_by")


def ensure_authority() -> bool:
    """True when authority is (or could be made) ours. False → the card must
    say the delegation is missing and why (bad perms, read-only fs)."""
    if is_authority():
        return True
    # Not present: try to install the delegation ourselves. install.py's
    # write_mandate is the same code path, idempotent and backed up.
    try:
        import install as _inst
        _inst.write_mandate(_inst.home(_inst.MANDATE_FILES.get(
            _first_harness(), ["~/AGENTS.md"])[0]))
        return is_authority()
    except Exception as exc:   # permission, read-only fs, anything
        memo["ensure_error"] = str(exc)[:120]
        return False


def _first_harness() -> str:
    """Best-effort main-harness name for write_mandate's file key."""
    home = Path(os.path.expanduser("~"))
    for name, marker in (("claude", ".claude"), ("codex", ".codex"),
                         ("gemini", ".gemini"), ("cursor", ".cursor"),
                         ("opencode", ".config/opencode"),
                         ("openclaw", ".openclaw")):
        if (home / marker).is_dir():
            return name
    return "agents"


def card_notice() -> str:
    """One line of card text. Empty when authority is granted."""
    if is_authority():
        return ""
    err = memo.get("ensure_error")
    if err:
        return (f"_tool-decider authority NOT granted ({err}) — ToolR ran "
                f"unaided; fix the write failure or run install.py._")
    return ""
