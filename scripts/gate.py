#!/usr/bin/env python3
"""Optional enforcement: make "load skills, then work" actually hold.

A prompt hook can only advise — the model may ignore the card. The one lever
that binds is a PreToolUse deny: when the router named skills for this prompt
and none has been loaded yet, the first Edit/Write is refused once, with the
reason naming what to invoke.

Wired by `install.py --enforce` as two hooks:

    PreToolUse  (Edit|Write|MultiEdit|NotebookEdit)  gate.py --check
    PostToolUse (Skill)                              gate.py --loaded

Deliberately deny-ONCE per prompt: a second refusal is how enforcement hooks
turn into loops. After one refusal the decision is the model's again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402

STATE = rc.index_path().parent / "last_route.json"


def _read() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass


def allow() -> int:
    """Say nothing: an empty response leaves the normal permission flow alone."""
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="PreToolUse: gate edits")
    ap.add_argument("--loaded", action="store_true", help="PostToolUse(Skill): mark satisfied")
    args = ap.parse_args()

    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        payload = {}
    state = _read()

    if args.loaded:
        state["needs_skills"] = False
        _write(state)
        # Record the loaded pick into learned.json — the usage-prior signal
        # (research note R7). The Skill tool's input carries the loaded name;
        # record the CARD name (match kind-prefixed picks). A skill that is
        # actually loaded is the strongest automatic label available.
        try:
            cmd = ""
            try:
                ti = payload.get("tool_input") or {}
                cmd = str(ti.get("command") or ti.get("skill") or "")
            except Exception:
                cmd = ""
            name = cmd.split(":")[-1].strip().lower()
            if not name:
                # fall back: any state pick whose name appears in the tool input
                blob = json.dumps(payload.get("tool_input") or {}).lower()
                for p in state.get("picks") or []:
                    n = p.split(":")[-1].lower()
                    if n and n in blob:
                        name = n
                        break
            if name:
                path = rc.index_path().parent / "learned.json"
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    data = {}
                cur = float(data.get(name, 0))
                w = 0.25
                data[name] = min(cur + w, 2.0)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        except Exception:
            pass  # learning must never break the hook
        return allow()

    if not args.check:
        return allow()

    pid = state.get("prompt_id")
    if not pid:  # never routed (no picks recorded for any prompt)
        return allow()
    if not state.get("needs_skills") or state.get("denied_for") == pid:
        return allow()

    picks = state.get("picks") or []
    if not picks:
        return allow()

    state["denied_for"] = pid  # one refusal per prompt, never a loop
    _write(state)
    tool = payload.get("tool_name", "this edit")
    names = ", ".join(f"Skill({n})" for n in picks[:3])
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"tool-router: load the routed skills before {tool}. Invoke {names} first "
            f"(or say in one line why they do not apply, then retry — this gate fires once "
            f"per request)."
        ),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
