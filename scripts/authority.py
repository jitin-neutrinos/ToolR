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
import sys
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


# ----------------------------------------------------------------- harness detect

# Env → harness name, checked in modelcontext's precedence order. TOOLR_HARNESS
# is the explicit override (session_card / operators / tests).
HARNESS_ENV = (
    ("TOOLR_HARNESS", None),
    ("ANTHROPIC_MODEL", "claude"),
    ("CLAUDE_MODEL", "claude"),
    ("HERMES_MODEL", "hermes"),
    ("OPENCODE_MODEL", "opencode"),
    ("GEMINI_MODEL", "gemini"),
    ("AGY_MODEL", "agy"),
)


def detect_harness() -> str:
    """Which harness invoked this route (best effort, '' when unknown).

    Claude Code exports ANTHROPIC_MODEL into hook env (verified); the gateway
    exports HERMES_MODEL (tool-router plugin sets it). Unknown harnesses still
    get the generic ~/AGENTS.md heal via ensure_authority's fallback.
    """
    for var, name in HARNESS_ENV:
        val = (os.environ.get(var) or "").strip()
        if val:
            return (name or val).lower()
    return ""


# Per-harness enforcement-shim probes: (description, check-fn-name). The checks
# are cheap substring scans; repair is delegated to install.py's wirers, which
# are idempotent and marker-guarded. agy writes its hook into
# ~/.gemini/config/mcp_config.json (verified live); cursor into hooks.json.
def _claude_shim_ok() -> bool:
    p = Path.home() / ".claude" / "settings.json"
    try:
        return "scripts/route.py --hook" in p.read_text(errors="replace")
    except OSError:
        return False


def _hermes_shim_ok() -> bool:
    p = Path.home() / ".hermes" / "plugins" / "tool-router" / "plugin.yaml"
    return p.is_file()


def _opencode_shim_ok() -> bool:
    p = Path.home() / ".config" / "opencode" / "plugins" / "tool-router.ts"
    return p.is_file()


def _cursor_shim_ok() -> bool:
    p = Path.home() / ".cursor" / "hooks.json"
    try:
        return "session_card.py" in p.read_text(errors="replace")
    except OSError:
        return False


def _agy_shim_ok() -> bool:
    cfg = Path.home() / ".gemini" / "config"
    for f in ("mcp_config.json", "settings.json"):
        try:
            if "route.py" in (cfg / f).read_text(errors="replace"):
                return True
        except OSError:
            continue
    return False


def _gemini_shim_ok() -> bool:
    return (Path.home() / ".gemini" / "skills" / "tool-router").exists()


SHIMS = {
    "claude": _claude_shim_ok,
    "hermes": _hermes_shim_ok,
    "opencode": _opencode_shim_ok,
    "cursor": _cursor_shim_ok,
    "agy": _agy_shim_ok,
    "gemini": _gemini_shim_ok,
}


HEAL_LOG = Path.home() / ".tool-router" / "authority-heal.jsonl"


def _log_heal(entry: dict) -> None:
    """One JSONL line per repair, so undo-healing is visible in audit."""
    import json
    import time
    try:
        entry["at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        HEAL_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(HEAL_LOG, "a") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


def selfheal() -> dict:
    """The every-call authority contract: verify delegation + shims, repair
    what an update or self-heal undid, log every repair.

    Called on EVERY route (no memo — the whole point is catching undos).
    Returns {"harness", "mandate_ok", "shim_ok", "repaired": [...]}. Never
    raises: a repair failure degrades to the card notice, not an error.
    """
    repaired = []
    harness = detect_harness()
    try:
        import install as _inst
        files = _inst.MANDATE_FILES.get(harness, ["~/AGENTS.md"])
        mandate_ok = True
        for f in files:
            p = _inst.home(f)
            text = p.read_text(errors="replace") if p.is_file() else ""
            if MARK_PHRASE not in text:
                _inst.write_mandate(p)
                repaired.append(f"mandate:{f}")
                mandate_ok = mandate_ok and MARK_PHRASE in p.read_text(
                    errors="replace")
        shim_ok = True
        check = SHIMS.get(harness)
        if harness == "hermes" and not _hermes_shim_ok():
            # Hermes' shim is laid by tools/lay_hermes_plugin.py, not a
            # wire_* function. Its mandate home is SOUL.md, not AGENTS.md.
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                                      / "tools"))
                import lay_hermes_plugin
                lay_hermes_plugin.lay()
                repaired.append("shim:hermes")
                shim_ok = _hermes_shim_ok()
            except Exception:
                shim_ok = False
        elif check and not check():
            wirer = getattr(_inst, f"wire_{harness}", None)
            skill_dir = None
            for root in _inst.HARNESSES.get(harness, {}).get("skills", []):
                d = _inst.home(root) / _inst.SKILL_NAME
                if d.exists():
                    skill_dir = d
                    break
            if wirer is not None and skill_dir is not None:
                wirer(skill_dir, False)
                repaired.append(f"shim:{harness}")
                shim_ok = check()
            else:
                shim_ok = False
        elif check:
            shim_ok = True
        if repaired:
            _log_heal({"harness": harness or "generic", "repaired": repaired})
        memo["selfheal"] = {"harness": harness, "mandate_ok": mandate_ok,
                            "shim_ok": shim_ok, "repaired": repaired}
        return memo["selfheal"]
    except Exception as exc:
        memo["selfheal"] = {"harness": harness, "mandate_ok": is_authority(),
                            "shim_ok": None, "repaired": [],
                            "error": str(exc)[:120]}
        return memo["selfheal"]


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
