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
import re
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
        # Carry the adoption ledger forward (rewritten per prompt; kept/total
        # feed the 75% floor in gate.py --check). 'total' counts this routed
        # prompt — the denominator of the harness+model load ratio.
        try:
            prev = json.loads(STATE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev = {}
        led = prev.get("adoption") or {"kept": 0, "total": 0}
        led["total"] = int(led.get("total", 0)) + 1
        STATE.write_text(json.dumps({"picks": picks, "prompt_id": prompt_id,
                                     "needs_skills": skills_needed,
                                     "adoption": led, "at": int(time.time())}),
                         encoding="utf-8")
    except OSError:
        pass


def card_for(prompt: str, cwd: Path, prompt_id: str = "", rewrite: bool = True,
             n_override: int | None = None) -> str:
    cfg = rc.load_config()
    if not cfg.get("enabled", True):
        return ""
    index = get_index(cwd, cfg)
    try:
        import pipeline
        result = pipeline.run(prompt, cwd, cfg, index, rewrite=rewrite,
                              n_override=n_override)
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


def _print_usage_hint(have: str, want: str) -> None:
    """On a bad flag, print the real invocation instead of argparse's wall.

    Measured 2026-10-05: an agent ran `route --top 20 "..."` and got
    "unrecognized arguments: --top" plus a usage block that names no useful
    example. The agent then had to guess. A one-line worked example is the
    difference between the tool being used and being abandoned.
    """
    print(f"tool-router: '{have}' is not a valid option. Correct form:\n"
          f"    ~/.tool-router/route {want} \"<your request>\"\n"
          f"  options: -n/--top N (how many picks), --no-rewrite (skip the removed "
          f"rewrite stage), --source \"<need>\" (search free registries), "
          f"--record a,b (log what you used), --selftest\n"
          f"  example: ~/.tool-router/route --top 10 \"revamp the chat composer\"",
          file=sys.stderr)


class _ArgParser(argparse.ArgumentParser):
    """argparse that answers an unknown flag with a usable example.

    The suggestion is the CLOSEST real option, never an echo of what was typed:
    `--topp` must not come back as `--top --topp`, which is the same failure with
    extra words. difflib is stdlib and the option list is tiny, so this is free.
    """
    OPTIONS = ("--top", "-n", "--count", "--no-rewrite", "--source",
               "--source-remove", "--finder", "--finder-install",
               "--record", "--json", "--cwd", "--event",
               "--hook", "--selftest", "--help", "-h")

    def error(self, message):  # noqa: D401
        import difflib
        m = re.search(r"unrecognized arguments: (.+)$", str(message))
        given = m.group(1).split()[0] if m else str(message)
        near = difflib.get_close_matches(given, self.OPTIONS, n=1, cutoff=0.6)
        if near:
            want = near[0] if near[0] != given else "--top"
        elif given.startswith("-") and re.search(r"\d", given):
            want = "--top"          # --top 20 / -n 20 / --top=20
        else:
            want = ""
        _print_usage_hint(given, want)
        self.exit(2)


def main() -> int:
    ap = _ArgParser(
        prog="~/.tool-router/route",
        description="Route a request to the most relevant installed skills, MCPs, "
                    "subagents and commands. Prints a router card.",
        epilog="example: ~/.tool-router/route --top 10 \"revamp the chat composer\"",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("prompt", nargs="*", help="prompt text (omit when using --hook)")
    ap.add_argument("--hook", action="store_true", help="read harness hook JSON from stdin")
    ap.add_argument("--cwd", default=None)
    ap.add_argument("--record", default=None, help="comma-separated skills that were actually used")
    ap.add_argument("--json", action="store_true", help="emit hook JSON even outside --hook")
    ap.add_argument("--event", default="UserPromptSubmit",
                    help="hook event name to echo back (Gemini CLI uses BeforeAgent)")
    ap.add_argument("--no-rewrite", action="store_true",
                    help="accepted for compatibility; the rewrite stage was "
                         "removed 2026-10-05 so this changes nothing")
    ap.add_argument("--source", action="store_true",
                    help="HITL: search registries for the query, print screened candidates")
    ap.add_argument("--finder", action="store_true",
                    help="interactive skill finder: search + inspect real skill "
                         "bodies + IOC/typosquat/screen + pin, all before approval")
    ap.add_argument("--finder-install", default=None, metavar="N[:SHA]",
                    help="install finder candidate N at its reviewed commit, "
                         "then verify the route fires (HITL: you choose N)")
    ap.add_argument("--source-remove", default=None, metavar="SKILL",
                    help="undo a sourced skill (removes sourced/SKILL + reindexes)")
    ap.add_argument("--selftest", action="store_true", help="run the router's own checks")
    ap.add_argument("-n", "--top", "--count", dest="top_n", type=int, default=None,
                    metavar="N",
                    help="return top N capabilities; overrides any number named in "
                         "the prompt (clamped to 1-50)")
    ap.add_argument("--deferred", default=None, metavar="JSON",
                    help=argparse.SUPPRESS)   # internal: background sourcing sweep
    args = ap.parse_args()

    if args.deferred:
        # Background child of the sourcing stage. Everything slow lives here:
        # the gap oracle, the registry search, the relevance judge, installs.
        try:
            payload = json.loads(args.deferred)
            import source as _src
            hit, _entry = _src.auto_install_sync(
                payload.get("prompt", ""), payload.get("query", ""),
                rc.load_config(), [])
            print(json.dumps({"installed": (hit or {}).get("installed"),
                              "ok": (hit or {}).get("ok", False)}))
        except Exception as exc:
            print(json.dumps({"error": repr(exc)[:200]}))
        return 0

    if args.selftest:
        os.execv(sys.executable, [sys.executable, str(SELF.parent / "selftest.py")])

    if args.source_remove:
        try:
            import source as _src
            import gaptrack as _gt
            name = args.source_remove
            detail = _src.distribute_skill(name, remove=True)
            src_dir = Path.home() / ".hermes/skills/sourced" / name
            if src_dir.exists():
                import shutil as _sh
                _sh.rmtree(src_dir)
            wiring = _src.wire_fleet()
            # clear the lockout so the intent can source a better candidate later
            _gt_data = _gt.load()
            cleared = [k for k, v in _gt_data.items()
                       if v.get("installed") == name]
            for k in cleared:
                _gt.unmark_installed(k, reason=f"removed {name}")
            _src._log({"event": "source_remove", "skill": name,
                       "intents_reopened": cleared})
            print(f"removed {name}: harnesses={detail}, wiring={wiring}, "
                  f"intents_reopened={len(cleared)}")
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

    if args.finder:
        prompt = " ".join(args.prompt).strip()
        if not prompt:
            print("usage: route.py --finder \"<what you need>\"")
            return 0
        try:
            import source as _src
            cands = _src.propose(prompt)   # annotate + body+script screen inside
        except Exception as exc:
            print(f"finder failed (fail-open): {exc!r}")
            return 0
        if not cands:
            print(f"No free candidates found for: {prompt}")
            return 0
        print(f"# Toutur finder: {prompt}\n"
              f"# Each candidate was inspected at its REAL SKILL.md body and\n"
              f"# scripts, injection-screened, IOC-scanned and typosquat-checked.\n"
              f"# NOTHING is installed until you choose:\n"
              f"#   route --finder-install <n>  (pins to the reviewed commit)\n")
        for i, c in enumerate(cands, 1):
            body = c.get("body") or {}
            flags = [f"screen={c.get('screen') or 'unscreened'}"]
            if c.get("ioc"):
                flags.append("IOC:" + ",".join(c["ioc"][:3]))
            ts = c.get("typosquat") or {}
            if ts.get("risk"):
                flags.append("TYPOSQUAT:" + (ts.get("note") or "")[:40])
            if body.get("error"):
                flags.append(f"body-error:{str(body['error'])[:40]}")
            installs = c.get("installs")
            installs = f"{installs:,}" if isinstance(installs, int) else "?"
            sha = body.get("sha") or "?"
            print(f"{i}. [{c['kind']}] {c['name']}  ({c.get('source')}, "
                  f"{installs} installs, commit {sha})")
            print(f"   id: {c['identifier']}")
            print(f"   {(c.get('description') or '')[:150]}")
            print(f"   flags: {'; '.join(flags)}")
            snippet = " ".join((body.get("text") or "").split())[:200]
            if snippet:
                print(f"   body: {snippet}...")
        return 0

    if args.finder_install:
        prompt = " ".join(args.prompt).strip()
        spec = (args.finder_install or "").split(":", 1)
        try:
            n, want_sha = int(spec[0]), (spec[1] if len(spec) > 1 else None)
            import source as _src
            cands = _src.propose(prompt)
            if not (1 <= n <= len(cands)):
                print(f"candidate {n} out of range (1..{len(cands)}); "
                      f"re-run --finder to list candidates")
                return 0
            c = cands[n - 1]
            sha = (c.get("body") or {}).get("sha")
            if want_sha and sha and want_sha != sha:
                print(f"REFUSED: reviewed sha {want_sha} != current {sha}. "
                      f"The repo moved since review — re-run --finder and "
                      f"re-review before installing.")
                return 1
            blocked = list(c.get("ioc") or [])
            if (c.get("typosquat") or {}).get("risk"):
                blocked.append("typosquat")
            if blocked:
                print(f"REFUSED: candidate {n} flagged ({'; '.join(blocked)}). "
                      f"Flagged candidates are never installed.")
                return 1
            if c.get("kind") == "mcp":
                ok, detail = _src.install_mcp(c)
            else:
                ok, detail = _src.install_skill(c)
            if not ok:
                print(f"install failed: {detail}")
                return 1
            wired: dict = {}
            if c.get("kind") == "skill":
                wired = _src.wire_fleet()
                wired["harnesses"] = _src.distribute_skill(c["name"])
                try:
                    k = _src.gaptrack.resolve_key(prompt)
                    if k:
                        _src.gaptrack.mark_installed(k, c["name"])
                except Exception:
                    pass
            print(f"installed {c['name']} "
                  f"({'pinned @ ' + sha if sha else 'unpinned'})")
            print(f"wiring: reindex={wired.get('reindex')}, "
                  f"fleet={wired.get('fleet')}, "
                  f"harnesses={wired.get('harnesses')}")
            if c.get("kind") == "skill":
                v = _src.verify_route(prompt, args.cwd or ".")
                print("verify: ROUTE FIRES" if v.get("ok") else
                      f"verify: not in top picks yet — {str(v.get('detail'))[:120]}")
            return 0
        except Exception as exc:
            print(f"finder-install failed (fail-open): {exc!r}")
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
                        rewrite=not args.no_rewrite, n_override=args.top_n)
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
