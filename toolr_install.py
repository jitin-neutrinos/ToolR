#!/usr/bin/env python3
"""toolr_install.py — the ToolR rich TUI installer.

Wraps install.py's detection/wiring with a branded terminal UI:
pixel-art logo -> step-by-step progress -> per-harness status table.

  python3 toolr_install.py            # install everywhere detected
  python3 toolr_install.py --demo     # render the TUI without changing anything
  python3 toolr_install.py --check    # detection report, no TUI
Plus install.py's flags pass through (--harness, --copy, --no-hooks, ...).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import toolr_tui as T  # noqa: E402

# display names for the detected harness keys in install.py
DISPLAY = {
    "claude": "Claude Code",
    "codex": "Codex CLI",
    "gemini": "Gemini CLI",
    "cursor": "Cursor",
    "opencode": "OpenCode",
    "agy": "Antigravity (agy)",
    "openclaw": "OpenClaw",
    "agents": "~/.agents (shared)",
}
SKILL_PATHS = {
    "claude": "~/.claude/skills", "codex": "~/.agents/skills",
    "gemini": "~/.gemini/skills", "cursor": "~/.cursor/skills",
    "opencode": "~/.config/opencode/skills", "agy": "~/.gemini/config/skills",
    "openclaw": "~/.openclaw/skills", "agents": "~/.agents/skills",
}


def _detect() -> list[str]:
    """Mirror install.py's detection without importing its side effects."""
    out = []
    probes = {
        "claude": "~/.claude", "codex": "~/.codex", "gemini": "~/.gemini",
        "cursor": "~/.cursor", "opencode": "~/.config/opencode",
        "agy": "~/.gemini/config", "openclaw": "~/.openclaw", "agents": "~/.agents",
    }
    home = Path(os.path.expanduser("~"))
    for name, p in probes.items():
        if any((home / pp.lstrip("~/")).is_dir() for pp in [p]):
            out.append(name)
    # agy and gemini both probe ~/.gemini*; keep the order claude-first
    return [n for n in ("claude", "codex", "gemini", "cursor", "opencode",
                        "agy", "openclaw", "agents") if n in out]


def _already_installed(name: str) -> bool:
    home = Path(os.path.expanduser("~"))
    root = SKILL_PATHS.get(name, "").replace("~", str(home))
    return bool(root) and (Path(root) / "tool-router").exists()


def _step(msg: str, done: bool = False) -> None:
    mark = T._ansi("✔", (80, 220, 120)) if done else T._ansi("▸", T.CYAN_RGB)
    print(f"  {mark} {msg}", flush=True)


def run_demo(args) -> int:
    found = _detect()
    rows = [(DISPLAY.get(h, h),
             "already" if _already_installed(h) else "installed",
             SKILL_PATHS.get(h, "")) for h in found]
    print(T.banner("ToolR installer (demo)"))
    print(T.render_steps(3, [DISPLAY.get(h, h) for h in found]))
    print(T.render_harness_table(rows))
    print()
    print(T._ansi("  (demo — nothing was changed)", (140, 145, 155)))
    return 0


def run_install(args) -> int:
    t0 = time.time()
    print(T.banner("ToolR installer"))
    print(T.render_steps(0), flush=True)
    time.sleep(0.4)

    found = _detect()
    print("\r" + T.render_steps(2, [DISPLAY.get(h, h) for h in found]), flush=True)
    time.sleep(0.5)

    if not found:
        print(T.render_steps(3, None))
        print(T._ansi("\n  No supported harness found on this machine.", (240, 90, 90)))
        print(T._ansi("  Pass --hordaness NAME to force one, or install a harness first.",
                      (240, 90, 90)))
        return 1

    # real install via install.py, streaming its output as sub-lines
    cmd = [sys.executable, str(HERE / "install.py")] + args.forward
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    rows: list[tuple[str, str, str]] = []
    for line in proc.stdout:
        line = line.rstrip()
        if not line:
            continue
        print(T._ansi("      " + line, (150, 158, 170)), flush=True)
        if line.startswith("detected harnesses:"):
            names = line.split(":", 1)[1].strip()
            rows = [(DISPLAY.get(n, n) or n, "installed", "") for n in names.split(", ") if n]
    proc.wait()

    print("\r" + T.render_steps(4), flush=True)
    time.sleep(0.3)
    if proc.returncode == 0:
        print("\r" + T.render_steps(7), flush=True)
    ms = (time.time() - t0) * 1000
    print(T.render_harness_table(rows or [(DISPLAY.get(h, h), "installed", "") for h in found]))
    print()
    hdr = T._ansi(f"  ToolR installed in {ms:.0f} ms — run:", (80, 220, 120))
    ex = T._ansi('  ~/.tool-router/route "your request here"', (245, 248, 252))
    print(hdr)
    print(ex)
    return proc.returncode


def main() -> int:
    ap = argparse.ArgumentParser(prog="toolr")
    ap.add_argument("--demo", action="store_true", help="render the TUI, change nothing")
    ap.add_argument("--check", action="store_true")
    pre, rest = ap.parse_known_args()
    if pre.check:
        import install
        return install.main()
    if pre.demo:
        return run_demo(pre)
    return run_install(argparse.Namespace(forward=rest))


if __name__ == "__main__":
    raise SystemExit(main())
