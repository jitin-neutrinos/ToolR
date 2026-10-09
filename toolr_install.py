#!/usr/bin/env python3
"""toolr_install.py — the Toutur TUI installer.

Wraps install.py's detection/wiring with the branded terminal UI: the real
monogram (pixel-extracted from the icon) sweeps in at 60fps, steps advance
as install.py actually reports them, and the run ends on a status table +
light-wave finale. Colors mirror the landing page; piped output gets the
same facts with zero escape codes.

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
    "hermes": "Hermes",
    "agents": "~/.agents (shared)",
}
SKILL_PATHS = {
    "claude": "~/.claude/skills", "codex": "~/.agents/skills",
    "gemini": "~/.gemini/skills", "cursor": "~/.cursor/skills",
    "opencode": "~/.config/opencode/skills", "agy": "~/.gemini/config/skills",
    "openclaw": "~/.openclaw/skills", "hermes": "~/.hermes/skills/tools",
    "agents": "~/.agents/skills",
}


def _detect() -> list[str]:
    """Mirror install.py's detection without importing its side effects."""
    out = []
    probes = {
        "claude": "~/.claude", "codex": "~/.codex", "gemini": "~/.gemini",
        "cursor": "~/.cursor", "opencode": "~/.config/opencode",
        "agy": "~/.gemini/config", "openclaw": "~/.openclaw",
        "hermes": "~/.hermes", "agents": "~/.agents",
    }
    home = Path(os.path.expanduser("~"))
    for name, p in probes.items():
        if (home / p.lstrip("~/")).is_dir():
            out.append(name)
    # agy and gemini both probe ~/.gemini*; keep the canonical order
    return [n for n in ("claude", "codex", "gemini", "cursor", "opencode",
                        "agy", "openclaw", "hermes", "agents") if n in out]


def _already_installed(name: str) -> bool:
    home = Path(os.path.expanduser("~"))
    root = SKILL_PATHS.get(name, "").replace("~", str(home))
    return bool(root) and (Path(root) / "tool-router").exists()


def _progress_reader(proc, on_line) -> None:
    """Stream install.py's stdout; each line goes to on_line(line)."""
    for raw in proc.stdout:
        line = raw.rstrip()
        if line:
            on_line(line)


def run_demo(args) -> int:
    found = _detect()
    rows = [(DISPLAY.get(h, h),
             "already" if _already_installed(h) else "installed",
             SKILL_PATHS.get(h, "")) for h in found]
    print(T.banner("Toutur installer (demo)"))
    print(T.render_steps(3, [DISPLAY.get(h, h) for h in found]))
    print(T.render_harness_table(rows))
    print()
    print(T.fg("  (demo — nothing was changed)", T.MUTED_RGB))
    return 0


def run_install(args) -> int:
    t0 = time.time()
    try:
        import toolr_anim as A
    except Exception:
        A = None

    tty = A and A._tty() if A else False

    # Intro: static banner when piped; monogram + title fade-in on a TTY.
    if tty:
        art = T.render_logo().splitlines()
        print("\n".join(art))
        print(f"\n  {T.fg('Toutur installer', T.TEXT_RGB)}\n"
              f"  {T.fg(T.TAGLINE, T.BLUE_RGB)}\n")
    else:
        print(T.banner("Toutur installer"))

    # Step 1 — probe
    if A:
        with A.Spinner("probing harnesses…") as s:
            found = _detect()
            time.sleep(0.25)
            s.stop(final=f"{len(found)} harness(es) found: "
                   + ", ".join(DISPLAY.get(h, h) for h in found))
    else:
        found = _detect()
        print(f"  ▸ {len(found)} harness(es) found: "
              + ", ".join(DISPLAY.get(h, h) for h in found))

    if not found:
        print(T.fg("\n  No supported harness found on this machine.", T.FAIL_RGB))
        print(T.fg("  Pass --harness NAME to force one, or install a harness first.",
                   T.FAIL_RGB))
        return 1

    # Step 2 — install. install.py streams; we ride its real output.
    print()
    cmd = [sys.executable, str(HERE / "install.py")] + args.forward
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    rows: list[tuple[str, str, str]] = []
    harness_count = len(found)

    if A and tty:
        import threading
        import itertools
        done = threading.Event()
        tick = itertools.count()

        def pump():
            assert proc.stdout is not None
            for raw in proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                if line.startswith("detected harnesses:"):
                    names = line.split(":", 1)[1].strip()
                    rows[:] = [(DISPLAY.get(n, n) or n, "installed", "")
                               for n in names.split(", ") if n]
                sys.stdout.write("\r\033[K" + T.fg("      " + line, T.MUTED_RGB) + "\n")
                sys.stdout.flush()
                next(tick)
            done.set()

        threading.Thread(target=pump, daemon=True).start()
        last = 0.0
        while not done.is_set():
            frac = min(0.9, (time.time() - t0) / 4.0)
            now = time.perf_counter()
            if now - last > 1 / 60:
                sys.stdout.write("\r\033[K" + A.progress(A.ease_out_expo(frac) * 0.9,
                                                         label="laying skills + wiring hooks"))
                sys.stdout.flush()
                last = now
            time.sleep(1 / 60)
        proc.wait()
        sys.stdout.write("\r\033[K" + A.progress(1.0, label="done") + "\n")
        sys.stdout.flush()
    else:
        assert proc.stdout is not None
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            print(T.fg("      " + line, T.MUTED_RGB), flush=True)
            if line.startswith("detected harnesses:"):
                names = line.split(":", 1)[1].strip()
                rows = [(DISPLAY.get(n, n) or n, "installed", "")
                        for n in names.split(", ") if n]
        proc.wait()

    print()
    ms = (time.time() - t0) * 1000
    if not rows:
        rows = [(DISPLAY.get(h, h),
                 "already" if _already_installed(h) else "installed",
                 SKILL_PATHS.get(h, "")) for h in found]
    print(T.render_harness_table(rows))
    print()
    if proc.returncode == 0 and A:
        A.celebrate([
            f"Toutur installed in {ms:.0f} ms across {harness_count} harness(es)",
            'try:  ~/.tool-router/route "your request here"',
        ])
    else:
        mark = T.fg("✔", T.BLUE2_RGB) if proc.returncode == 0 else T.fg("✘", T.FAIL_RGB)
        print(f"  {mark} Toutur {'installed' if proc.returncode == 0 else 'FAILED'} in {ms:.0f} ms")
        print(T.fg('  ~/.tool-router/route "your request here"', T.TEXT_RGB))
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
