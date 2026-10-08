#!/usr/bin/env python3
"""toolr_tui.py — the Toutur installer's terminal UI (rich, brand-colored).

Pixel-art logo of the wordmark (white Toutur on navy badge, cyan accent
square), step-by-step installation progress, per-harness status lines.

Run modes:
  show          just render the logo + banner (for demos/screenshots)
  install       run the full installation (delegates to install.py logic)
"""
from __future__ import annotations

import sys

# ---- brand ------------------------------------------------------------
NAVY = "#0a1a3a"      # badge background (deep navy, Toutur brand)
NAVY_RGB = (10, 26, 58)
WHITE = "#ffffff"
CYAN = "#00c8f0"      # the accent square
CYAN_RGB = (0, 200, 240)
GREY = "#7a8aa0"

# ---- pixel art --------------------------------------------------------
# Hand-coded from the generated wordmark (assets/toutur-wordmark.png):
# navy rounded badge, white bold Toutur, cyan 2x2 accent after the R's leg.
# Each char: N=navy, W=white, C=cyan, .=edge margin (terminal background).
LOGO = r"""
  .NNNNNNWWWWWWWNNNNNNNNNNNNNNNWWNNNNNWWWWWWNNNNNNNNNNNN.
  .NNNNNNWWWWWWWWNNNNNNNNNNNNNWWWWNNNNWWWWWWWWNNNNNNNNNN.
  .NNNNNNNWWNWWNNNNNWWWWWNNWWNNNWWWNNWWNNWWWWNNNNNNNNNNN.
  .NNNNNNNWWNWWNNNNWWNNNNWWWWNNNNWWNNWWNNNNNNNNNNNNNNNNN.
  .NNNNNNNWWNWWNNNNWWNNNNWWNNNNNNWWNNNWWWWWWNNNNNNNNNNNN.
  .NNNNNNNWWNWWNNNNWWNNNNWWNNNNNNWWNNNNWWNNNNNNNNNNNNNNN.
  .NNNNNNNWWNWWNNNNNWWWWWNNNNNNNNWWNNNNWWNNNNCCNNNNNNNNN.
  .NNNNNNNWWNWWNNNNNNNNNNNNNNNNNNWWNNNNWWNNNCCNNNNNNNNNN.
  .NNNNNNNWWNWWWWWWWWWWWWWWWNNNNNWWNNNNNWWWCCNNNNNNNNNNN.
  .NNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNNN.
"""


def _pixel(ch: str) -> str:
    if ch == "N":
        return _bg(BLOCK, NAVY_RGB)
    if ch == "W":
        return _ansi(BLOCK, (245, 248, 252))
    if ch == "C":
        return _ansi(BLOCK, CYAN_RGB)
    if ch == "U":
        return _ansi(BLOCK, (90, 105, 135))   # anti-alias pixel
    return " "

BLOCK = "\u2588"          # full block
TAGLINE = "Route every prompt to the right skill — before doing the work."
ASCII_ART = r"""
  ________          ___   ______  __ __  _______   __
 /_  __/ /  ___ _  / _ | / __/ |/ /_  / __/ _ | / /
  / / / _ \/ ' \ \/ __ |_\ \/ ,  // _/_\ \/ __ |/ _ \
 /_/ /_//_/_/_/_/_/____/___/_/|_/ /___/___/_/ |_____/
"""


def _rgb(hexs: str) -> tuple[int, int, int]:
    hexs = hexs.lstrip("#")
    return int(hexs[0:2], 16), int(hexs[2:4], 16), int(hexs[4:6], 16)


def _ansi(text: str, rgb: tuple[int, int, int]) -> str:
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"


def _bg(text: str, rgb: tuple[int, int, int]) -> str:
    return f"\033[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"


def render_logo(solid: bool = True) -> str:
    """The pixel art, each pixel one full-block, navy background for badge pixels."""
    out = []
    for line in LOGO.strip("\n").splitlines():
        out.append("".join(_pixel(ch) for ch in line))
    return "\n".join(out)


def banner(title: str = "Toutur installer") -> str:
    art = render_logo()
    t = _ansi(title, (255, 255, 255))
    tag = _ansi(TAGLINE, _rgb(CYAN))
    return f"{art}\n  {t}\n  {tag}\n"


# ---- rich TUI steps (pure stdlib when rich is absent) -------------------
STEPS = [
    "Starting installer",
    "Checking Python + dependencies",
    "Identifying installed harnesses",
    "Installing router into each harness",
    "Building the capability index",
    "Verifying routes",
    "Done",
]


def render_steps(done: int, harnesses: list[str] | None = None) -> str:
    lines = []
    for i, step in enumerate(STEPS):
        if i < done:
            mark = _ansi("\u2713", (80, 220, 120))       # green
            text = _ansi(step, (150, 160, 175))
        elif i == done:
            mark = _ansi("\u25b8", CYAN_RGB)             # cyan arrow
            text = _ansi(step, (255, 255, 255))
        else:
            mark = _ansi("\u2502", (90, 95, 105))
            text = _ansi(step, (90, 95, 105))
        lines.append(f"  {mark} {text}")
        if step == "Identifying installed harnesses" and harnesses and i == done:
            for h in harnesses:
                lines.append(f"      {_ansi('• ' + h, CYAN_RGB)}")
    return "\n".join(lines)


def render_harness_table(rows: list[tuple[str, str, str]]) -> str:
    """(harness, status) -> styled lines. status: installed / skipped / already / fail."""
    out = ["", _ansi("  Harness           Status", (200, 205, 215))]
    out.append("  " + _ansi("─" * 40, (60, 65, 75)))
    for name, status, detail in rows:
        color = {"installed": (80, 220, 120), "already": CYAN_RGB,
                 "skipped": (180, 180, 180), "fail": (240, 90, 90)}.get(status, (200, 200, 200))
        pad = name.ljust(16)
        out.append(f"  {_ansi(pad, (230, 230, 235))} {_ansi(status, color)}"
                   + (f"  {_ansi(detail, (130, 135, 145))}" if detail else ""))
    return "\n".join(out)
