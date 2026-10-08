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
        # Adoption ledger: a Skill load within a routed prompt = one "kept".
        # Counted once per prompt_id; feeds the 75% floor check in --check.
        try:
            led = state.get("adoption") or {"kept": 0, "total": 0}
            if state.get("prompt_id") and state.get("kept_for") != state.get("prompt_id"):
                led["kept"] = int(led.get("kept", 0)) + 1
                state["kept_for"] = state.get("prompt_id")
            state["adoption"] = led
        except Exception:
            pass
        # Honest per-harness ledger: this hook IS the claude numerator.
        try:
            import adoption as _adopt
            _adopt.record_load("claude")
        except Exception:
            pass
        state.pop("denied_for_second", None)
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

    # Owner policy (2026-10-08): the harness+model must load >=75% of the
    # routed picks. Ratio now comes from the honest per-harness ledger
    # (adoption.py): paired/routed for THIS harness, only over routes that
    # actually suggested skills. The old global kept/total (2/104) counted a
    # one-harness numerator against an all-harness denominator — a fictional
    # 2% that escalated every edit. Below the measurement floor (<3 routed),
    # no verdict: never escalate on absent data.
    try:
        import adoption as _adopt
        below_floor = _adopt.below_floor("claude", floor=float(
            rc.DEFAULT_CONFIG.get("adoption_floor", 0.75)))
    except Exception:
        below_floor = False

    state["denied_for"] = pid  # one refusal per prompt, never a loop
    if below_floor:
        state["denied_for_second"] = pid  # escalation window (see --loaded)
    _write(state)
    tool = payload.get("tool_name", "this edit")
    names = ", ".join(f"Skill({n})" for n in picks[:3])
    escalate = ("\nSECOND refusal: adoption is below the 75% floor (load what "
                "the router names, or route with --top 1 for a single pick)."
                if below_floor else "")
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"tool-router: load the routed skills before {tool}. Invoke {names} first "
            f"(or say in one line why they do not apply, then retry — this gate fires once "
            f"per request).{escalate}"
        ),
    }}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
