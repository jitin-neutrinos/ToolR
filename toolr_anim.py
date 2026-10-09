#!/usr/bin/env python3
"""Installer animation — 60fps-class spinner, gradient progress, finale.

Design contract (from the Copilot CLI banner post + the landing brand):
- one frame = one stdout write, cursor-home + clear-line, no full clears
- effects only on a real TTY with NO_COLOR unset; everything degrades to a
  plain static line otherwise (curl | bash logs stay clean)
- brand easing: ease_out_expo carries the cubic-bezier(.16,1,.3,1) feel of
  the landing page, so bars glide instead of stepping
- spinner/progress run on a daemon thread, joined on stop; PS5.1 conhost
  gets VT enabled once up front
"""
from __future__ import annotations

import itertools
import os
import shutil
import sys
import threading
import time

SPIN_FRAMES = ("◐", "◓", "◑", "◒")     # brand circle, 4 frames @ 60fps
TRAIL = "░▒▓"
BLUE = (34, 211, 238)                   # #22d3ee
BLUE2 = (56, 189, 248)                  # #38bdf8
TEXT = (248, 250, 252)
MUTED = (147, 155, 171)
HAIRLINE = (52, 58, 70)
FAIL = (248, 113, 113)
FRAME = 1 / 60                          # 60fps frame budget


def _tty() -> bool:
    if os.name == "nt":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _ansi(text: str, rgb: tuple[int, int, int]) -> str:
    if not _tty():
        return text
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"


def ease_out_expo(t: float) -> float:
    """Fast start, long silky settle — the landing page's easing curve."""
    if t >= 1.0:
        return 1.0
    return 1 - 2 ** (-10 * t)


class Spinner:
    """Inline brand spinner. Animated on a TTY, one static line otherwise."""

    def __init__(self, label: str):
        self.label = label
        self._frames = itertools.cycle(SPIN_FRAMES)
        self._alive = False
        self._t: threading.Thread | None = None
        self._stopped = False

    def _render(self) -> None:
        frame = next(self._frames)
        line = f"  {_ansi(frame, BLUE)} {_ansi(self.label, TEXT)}"
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()

    def _spin(self) -> None:
        while self._alive:
            self._render()
            time.sleep(FRAME)

    def start(self) -> "Spinner":
        if _tty():
            self._alive = True
            self._t = threading.Thread(target=self._spin, daemon=True)
            self._t.start()
        else:
            print(f"  ▸ {self.label}", flush=True)
        return self

    def stop(self, ok: bool = True, final: str | None = None) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._alive = False
        if self._t:
            self._t.join(timeout=0.3)
        mark = _ansi("✔", BLUE2) if ok else _ansi("✘", FAIL)
        msg = final if final is not None else self.label
        if _tty():
            sys.stdout.write("\r\033[K" + f"  {mark} {msg}\n")
        else:
            sys.stdout.write(f"  {msg}\n")
        sys.stdout.flush()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        if not self._stopped:
            self.stop(ok=exc_type is None)


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return (round(a[0] + (b[0] - a[0]) * k),
            round(a[1] + (b[1] - a[1]) * k),
            round(a[2] + (b[2] - a[2]) * k))


def progress(pct: float, width: int = 28, label: str = "") -> str:
    """Gradient bar (blue→blue-2) with a shimmer head; one rendered frame."""
    pct = max(0.0, min(1.0, pct))
    filled = width * pct
    cells = []
    for i in range(width):
        if i < filled:
            cells.append(_ansi("█", _blend(BLUE, BLUE2, i / max(1, width - 1))))
        elif i < filled + 2:                       # shimmer head
            cells.append(_ansi(TRAIL[min(2, int((filled + 2 - i) * 3))], BLUE))
        else:
            cells.append(_ansi("░", HAIRLINE))
    pct_txt = _ansi(f"{int(pct * 100):3d}%", MUTED)
    lbl = _ansi(f" {label}", MUTED) if label else ""
    return f"  {''.join(cells)} {pct_txt}{lbl}"


def animate_progress(run, total_s: float = 1.2, label: str = "") -> None:
    """Drive the bar at 60fps while the blocking `run()` executes.

    The bar eases toward 90% over `total_s` and snaps to 100% when the work
    returns — motion stays honest, never a fake finish.
    """
    if not _tty():
        print(f"  ▸ {label}…", flush=True)
        run()
        return
    done = threading.Event()
    def _work():
        run()
        done.set()
    t = threading.Thread(target=_work, daemon=True)
    t0 = time.perf_counter()
    t.start()
    while not done.is_set():
        elapsed = time.perf_counter() - t0
        frac = ease_out_expo(min(1.0, elapsed / total_s)) * 0.9
        sys.stdout.write("\r\033[K" + progress(frac, label=label))
        sys.stdout.flush()
        time.sleep(FRAME)
    t.join(timeout=5)
    sys.stdout.write("\r\033[K" + progress(1.0, label="done") + "\n")
    sys.stdout.flush()


def celebrate(lines: list[str]) -> None:
    """Success: a light-wave sweeps a hairline rule once, then the lines."""
    if not _tty():
        for l in lines:
            print(f"  {l}")
        return
    width = min(shutil.get_terminal_size((60, 20)).columns - 4, 56)
    frames = int(1.1 / FRAME)
    for i in range(frames):
        head = ease_out_expo(i / frames) * (width + 8) - 4
        bar = ""
        for j in range(width):
            d = j - head
            if -6 < d < 0:                          # the wave: bright head
                bar += _ansi("━", _blend(BLUE2, TEXT, 1 + d / 6))
            else:
                bar += _ansi("━", BLUE)
        sys.stdout.write("\r\033[K  " + bar)
        sys.stdout.flush()
        time.sleep(FRAME)
    print()
    for l in lines:
        print(f"  {_ansi('◆', BLUE)} {_ansi(l, TEXT)}")


if __name__ == "__main__":
    # self-demo: python3 toolr_anim.py
    with Spinner("probing harnesses…") as s:
        time.sleep(1.0)
        s.stop(final="harnesses probed")
    animate_progress(lambda: time.sleep(1.0), label="laying skills")
    celebrate(["Toutur installed in 812 ms", 'try:  ~/.tool-router/route "fix my flaky tests"'])
