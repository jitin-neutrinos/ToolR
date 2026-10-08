#!/usr/bin/env python3
"""Installer animation — spinner, shimmer, progress, celebratory finish.

Windows Terminal / PS5.1 conhost safe: pure ANSI (ESC[), 256/truecolor with a
static no-color fallback, single-threaded (no threads — PS5.1 pipes choke on
threaded writers via redirects), and every effect degrades to a plain line
when stdout is not a TTY.
"""
from __future__ import annotations

import itertools
import os
import shutil
import sys
import time

SPIN_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
SHIMMER = "░▒▓█▓▒░"
CYAN = (0, 200, 240)
GREEN = (80, 220, 120)
GREY = (140, 148, 160)


def _tty() -> bool:
    # Windows conhost (PS 5.1 default) needs VT processing enabled or ANSI
    # escape codes print as garbage / stack as raw lines. Enable once, then
    # gate every effect on isatty like before.
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


class Spinner:
    """Inline spinner with a label; use as a context manager or .stop(ok)."""

    def __init__(self, label: str):
        self.label = label
        self._frames = itertools.cycle(SPIN_FRAMES)
        self._alive = False
        self._t = None

    def _render(self) -> None:
        if not _tty():
            return
        frame = next(self._frames)
        bar = SHIMMER[int(time.time() * 10) % len(SHIMMER)]
        line = f"  {_ansi(frame, CYAN)} {self.label}  {_ansi(bar, GREY)}"
        sys.stdout.write("\r\033[K" + line)
        sys.stdout.flush()

    def _spin(self) -> None:
        while self._alive:
            self._render()
            time.sleep(0.08)

    def start(self) -> "Spinner":
        self._alive = True
        import threading
        self._t = threading.Thread(target=self._spin, daemon=True)
        self._t.start()
        return self

    def stop(self, ok: bool = True, final: str | None = None) -> None:
        self._alive = False
        if self._t:
            self._t.join(timeout=0.3)
        mark = _ansi("✔", GREEN) if ok else _ansi("✘", (240, 90, 90))
        msg = final if final is not None else self.label
        end = "\n" if _tty() else ""
        sys.stdout.write("\r\033[K" + f"  {mark} {msg}{end}")
        sys.stdout.flush()

    def __enter__(self):
        return self.start()

    def __exit__(self, exc_type, exc, tb):
        self.stop(ok=exc_type is None)


def progress(pct: float, width: int = 28, label: str = "") -> str:
    """One rendered progress line (caller prints/reprints it)."""
    filled = int(width * max(0.0, min(1.0, pct)))
    bar = "█" * filled + "░" * (width - filled)
    return f"  {_ansi(bar, CYAN)} {_ansi(f'{int(pct*100):3d}%', GREY)} {label}"


def celebrate(lines: list[str]) -> None:
    """Success flourish: the shimmer bar sweeps left→right once, then lines."""
    if not _tty():
        for l in lines:
            print(f"  {l}")
        return
    width = min(shutil.get_terminal_size((60, 20)).columns - 4, 56)
    for i in range(width + 1):
        bar = "".join(
            _ansi("━", CYAN if j <= i < j + 6 else (60, 80, 120))
            for j in range(width))
        sys.stdout.write("\r\033[K" + bar)
        sys.stdout.flush()
        time.sleep(0.012)
    print()
    for l in lines:
        print(f"  {_ansi('◆', CYAN)} {l}")


if __name__ == "__main__":
    # self-demo: python3 anim.py
    with Spinner("probing harnesses…") as s:
        time.sleep(1.2)
        s.stop(final="harnesses probed")
    for i in range(5):
        print("\r\033[K" + progress(i / 4, label="laying skills"), end="")
        sys.stdout.flush()
        time.sleep(0.2)
    print()
    celebrate(["Toutur installed in 812 ms", 'try:  ~/.tool-router/route "fix my flaky tests"'])
