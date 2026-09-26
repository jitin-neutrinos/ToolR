#!/usr/bin/env python3
"""Route one prompt to the skills, subagents, commands and MCP servers that fit it.

Three ways in:

  1. Claude Code hook (stdin gets the UserPromptSubmit JSON payload):
         python3 route.py --hook
     prints {"hookSpecificOutput": {...additionalContext...}} on stdout.

  2. Any harness, from the agent itself or a shell:
         python3 route.py "fix the flaky auth test"
     prints the routing card as plain markdown.

  3. Feedback (optional, makes future routing stickier in this repo):
         python3 route.py --record tdd,python-testing-patterns

Exit code is always 0 for routing: a router must never block a prompt.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402

SELF = Path(__file__).resolve()


BUILD_LOCK = rc.index_path().parent / "building.lock"


def _rebuild_detached(cwd: Path) -> None:
    """Refresh a stale index without making the user wait for it.

    The lock file (with PID + timestamp) stops the stampede where N rapid
    routes from a foreign cwd each spawned their own builder.
    """
    try:
        import os as _os
        import time as _time
        if BUILD_LOCK.is_file():
            try:
                data = json.loads(BUILD_LOCK.read_text())
                if _time.time() - float(data.get("at", 0)) < 120:
                    return  # a builder ran less than 2min ago; it will land
            except (OSError, ValueError):
                pass
        BUILD_LOCK.parent.mkdir(parents=True, exist_ok=True)
        BUILD_LOCK.write_text(json.dumps({"pid": _os.getpid(), "at": _time.time()}))
        subprocess.Popen(
            [sys.executable, str(SELF.parent / "index_build.py"), "--cwd", str(cwd), "--quiet"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError:
        pass


def get_index(cwd: Path, cfg: dict) -> dict:
    index = rc.load_index()
    if index is None:
        index = rc.build_index(cwd)
        rc.save_index(index)
        return index
    stale = (time.time() - index.get("built_at", 0)) > cfg["stale_hours"] * 3600
    if stale or index.get("cwd") != str(cwd):
        _rebuild_detached(cwd)
    return index


STATE = rc.index_path().parent / "last_route.json"


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(picks: list[str], prompt_id: str, skills_needed: bool) -> None:
    try:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps({"picks": picks, "prompt_id": prompt_id,
                                     "needs_skills": skills_needed, "at": int(time.time())}),
                         encoding="utf-8")
    except OSError:
        pass


def card_for(prompt: str, cwd: Path, prompt_id: str = "", rewrite: bool = True) -> str:
    cfg = rc.load_config()
    if not cfg.get("enabled", True):
        return ""
    index = get_index(cwd, cfg)
    try:
        import pipeline
        result = pipeline.run(prompt, cwd, cfg, index, rewrite=rewrite)
        card = result["card"]
        _save_state(result["picks"], prompt_id or _prompt_key(prompt), bool(result["picks"]))
        return card
    except Exception:
        # pipeline broke somewhere the single-stage path wouldn't — degrade, never block
        pass
    stack = rc.stack_tokens(cwd)
    ranked = rc.score(index, prompt, stack, rc.load_learned(), cfg.get("mcp_hints"))
    try:
        import laya_rerank
        ranked = laya_rerank.rerank(ranked, prompt, cfg)
    except Exception:
        pass  # rerank is advisory; never block routing
    sc = rc.scaffold(prompt)
    prev = _load_state().get("picks") if cfg.get("compact_repeats", True) else None
    card = rc.render_card(prompt, ranked, sc, stack, cfg, index.get("stats", {}), prev)
    picks = [r["name"] for r in rc.select(ranked, cfg)[0]]
    _save_state(picks, prompt_id or _prompt_key(prompt), bool(picks))
    return card


def _prompt_key(prompt: str) -> str:
    import hashlib
    return hashlib.sha1((prompt or "").encode("utf-8", "replace")).hexdigest()[:16]


def record(names: str) -> None:
    p = rc.index_path().parent / "learned.json"
    data = {}
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    for n in (x.strip().lower() for x in names.split(",") if x.strip()):
        data[n] = min(float(data.get(n, 0)) + 0.25, 2.0)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"recorded: {names}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("prompt", nargs="*", help="prompt text (omit when using --hook)")
    ap.add_argument("--hook", action="store_true", help="read harness hook JSON from stdin")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--record", default=None, help="comma-separated skills that were actually used")
    ap.add_argument("--json", action="store_true", help="emit hook JSON even outside --hook")
    ap.add_argument("--event", default="UserPromptSubmit",
                    help="hook event name to echo back (Gemini CLI uses BeforeAgent)")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="skip the LLM prompt-engineer stage (fast, routing only)")
    ap.add_argument("--source", action="store_true",
                    help="HITL: search registries for the query, print screened candidates")
    ap.add_argument("--source-remove", default=None, metavar="SKILL",
                    help="undo a sourced skill (removes sourced/SKILL + reindexes)")
    ap.add_argument("--selftest", action="store_true", help="run the router's own checks")
    args = ap.parse_args()

    if args.selftest:
        os.execv(sys.executable, [sys.executable, str(SELF.parent / "selftest.py")])

    if args.source_remove:
        try:
            import source as _src
            detail = _src.distribute_skill(args.source_remove, remove=True)
            src_dir = Path.home() / ".hermes/skills/sourced" / args.source_remove
            if src_dir.exists():
                import shutil as _sh
                _sh.rmtree(src_dir)
            wiring = _src.wire_fleet()
            print(f"removed {args.source_remove}: harnesses={detail}, wiring={wiring}")
        except Exception as exc:
            print(f"remove failed: {exc!r}")
        return 0

    if args.source:
        prompt = " ".join(args.prompt).strip()
        if not prompt:
            print("usage: route.py --source \"<what you need>\"")
            return 0
        try:
            import source as _src
            cands = _src.propose(prompt)
        except Exception as exc:
            print(f"sourcing failed (fail-open): {exc!r}")
            return 0
        if not cands:
            print(f"No free candidates found for: {prompt}")
            return 0
        print(f"# Candidates for: {prompt}\n"
              f"# Reply 'source install <n>' style approval to the agent; nothing is installed now.\n")
        for i, c in enumerate(cands, 1):
            screen = c.get("screen") or "unscreened"
            installs = c.get("installs")
            installs = f"{installs:,}" if isinstance(installs, int) else "?"
            print(f"{i}. [{c['kind']}] {c['name']}  ({c.get('source')}, "
                  f"{installs} installs, screen={screen})")
            print(f"   id: {c['identifier']}")
            print(f"   {(c.get('description') or '')[:160]}")
        return 0

    if args.record:
        record(args.record)
        return 0

    prompt, cwd = " ".join(args.prompt), args.cwd
    event, payload = args.event, {}
    if args.hook:
        raw = sys.stdin.read() or "{}"
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = {}
        prompt = payload.get("prompt") or payload.get("user_prompt") or prompt
        cwd = cwd or payload.get("cwd") or payload.get("workspace_dir")
        # Claude Code and Codex send UserPromptSubmit; Gemini CLI sends BeforeAgent.
        event = payload.get("hook_event_name") or payload.get("hookEventName") or event

    cwd_path = Path(cwd or os.getcwd()).resolve()
    reason = rc.skip_reason(prompt, payload if args.hook else None)
    if reason:
        if os.environ.get("TOOL_ROUTER_DEBUG"):
            print(f"tool-router: skipped ({reason})", file=sys.stderr)
        return 0

    try:
        card = card_for(prompt, cwd_path, str(payload.get("prompt_id", "")) if args.hook else "",
                        rewrite=not args.no_rewrite)
    except Exception as exc:  # a router must never break the user's turn
        if os.environ.get("TOOL_ROUTER_DEBUG"):
            print(f"tool-router error: {exc}", file=sys.stderr)
        return 0

    if not card:
        return 0
    if args.hook or args.json:
        # Codex caps injected context at ~2500 tokens by default; the card is far
        # smaller, but trim defensively so a huge index can never blow the cap.
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event,
            "additionalContext": card[:6000],
        }}))
    else:
        print(card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
