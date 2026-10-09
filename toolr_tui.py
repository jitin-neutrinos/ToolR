#!/usr/bin/env python3
"""toolr_tui.py — the Toutur installer's terminal UI.

Brand-exact: colors mirror toutur.jitinnair.com (void/midnight + blue/blue-2
accents) and the monogram is pixel-extracted from assets/toutur-icon.png
(lowercase t + motion streaks, 56x18) — not hand-drawn. '#' = white ink,
'+' = blue streak, '.' = blank.

Every helper gates color on _color_ok(): piped output / NO_COLOR gets a
clean static banner with zero escape codes, so `curl | bash` logs stay sane.
"""
from __future__ import annotations

import os
import shutil
import sys

# ---- brand (mirrors landing/index.html :root) --------------------------
VOID_RGB = (10, 10, 15)        # #0a0a0f  page background
MIDNIGHT_RGB = (18, 18, 26)    # #12121a  panel background
BLUE_RGB = (34, 211, 238)      # #22d3ee  primary accent
BLUE2_RGB = (56, 189, 248)     # #38bdf8  gradient pair / success
TEXT_RGB = (248, 250, 252)     # #f8fafc  primary text
MUTED_RGB = (147, 155, 171)    # #939bab  secondary text
HAIRLINE_RGB = (52, 58, 70)    # rules/dividers on dark
FAIL_RGB = (248, 113, 113)     # #f87171  failure only

# ---- monogram (pixel-extracted from assets/toutur-icon.png) ------------
MONOGRAM = [
    ".....................#######............................",
    ".....................#######............................",
    ".....................#######............................",
    ".................++++#######+++++++........+++++++++++++",
    "................+#################+.......+#############",
    "................+#################+.....+###############",
    ".................++++#######++++++....+######++++++++++.",
    "+++++++++++++++++++..#######......++++#####+............",
    "###################+.#######.+############+.............",
    "###################+.#######.+###########+..............",
    "+++++++++++++++++++..#######..++++++++++++++............",
    ".....................#######...........+++++++..........",
    ".....................#######............++++++++++++++++",
    ".....................#######..............++++++++++++++",
    ".....................########+...++.....................",
    ".....................+#############+....................",
    "......................+#############....................",
    "........................++#########+....................",
]
BLOCK = "\u2588"
TAGLINE = "Route every prompt to the right skill — before doing the work."


def _color_ok() -> bool:
    if os.environ.get("NO_COLOR") is not None:
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return sys.stdout.isatty()


def fg(text: str, rgb: tuple[int, int, int]) -> str:
    if not _color_ok():
        return text
    return f"\033[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"


def bg(text: str, rgb: tuple[int, int, int]) -> str:
    if not _color_ok():
        return text
    return f"\033[48;2;{rgb[0]};{rgb[1]};{rgb[2]}m{text}\033[0m"


# Back-compat: toolr_install.py calls T._ansi / T.CYAN_RGB.
def _ansi(text: str, rgb: tuple[int, int, int]) -> str:
    return fg(text, rgb)


CYAN_RGB = BLUE_RGB


def _px(ch: str, sweep: float = -1.0, x: int = 0, width: int = 56) -> str:
    if ch == "#":
        if sweep >= 0.0 and abs(x - sweep) < 2.5:
            return bg(BLOCK, BLUE_RGB)      # shimmer front: blue on white
        return fg(BLOCK, TEXT_RGB)
    if ch == "+":
        if sweep >= 0.0 and abs(x - sweep) < 2.5:
            return fg(BLOCK, TEXT_RGB)      # shimmer front: white on blue
        return fg(BLOCK, BLUE_RGB)
    return " "


def render_logo(t: float = -1.0) -> str:
    """Static monogram; 0<=t<=1 paints a left-to-right shimmer sweep.

    Terminals narrower than the art (58 cols) get the left portion of each
    row — the stem stays visible — instead of a missing banner.
    """
    term_w = shutil.get_terminal_size((60, 20)).columns
    sweep = t * (len(MONOGRAM[0]) + 6) - 3 if 0.0 <= t <= 1.0 else -1.0
    keep = max(20, term_w - 2)                # never render narrower than the stem
    rows = []
    for line in MONOGRAM:
        rows.append("  " + "".join(_px(ch, sweep, x)
                                   for x, ch in enumerate(line[:keep])))
    return "\n".join(rows)


def banner(title: str = "Toutur installer") -> str:
    return f"{render_logo()}\n\n  {fg(title, TEXT_RGB)}\n  {fg(TAGLINE, BLUE_RGB)}\n"


# ---- steps --------------------------------------------------------------
STEPS = [
    "Probing harnesses",
    "Laying skills into each harness",
    "Wiring per-prompt interceptors",
    "Building the capability index",
    "Verifying routes",
    "Done",
]


def render_steps(done: int, harnesses: list[str] | None = None,
                 detail: dict[int, str] | None = None) -> str:
    lines = []
    for i, step in enumerate(STEPS):
        if i < done:
            mark, text = fg("●", BLUE2_RGB), fg(step, MUTED_RGB)
        elif i == done:
            mark, text = fg("◐", BLUE_RGB), fg(step, TEXT_RGB)
        else:
            mark, text = fg("○", HAIRLINE_RGB), fg(step, HAIRLINE_RGB)
        lines.append(f"  {mark}  {text}")
        if detail and detail.get(i):
            lines.append(f"     {fg(detail[i], MUTED_RGB)}")
        if harnesses and step == "Probing harnesses" and i == done:
            for h in harnesses:
                lines.append(f"       {fg('· ' + h, BLUE_RGB)}")
    return "\n".join(lines)


def render_harness_table(rows: list[tuple[str, str, str]]) -> str:
    """(harness, status, path) -> aligned status table.

    Statuses: installed / already / skipped / fail. Success is blue-2 — the
    landing page is blue-only, green stays out of the brand.
    """
    marks = {"installed": ("✔", BLUE2_RGB), "already": ("●", BLUE_RGB),
             "skipped": ("○", MUTED_RGB), "fail": ("✘", FAIL_RGB)}
    if not rows:
        return ""
    name_w = max(len("Harness"), max(len(r[0]) for r in rows))
    stat_w = max(len("Status"), max(len(r[1]) for r in rows))
    loc_w = max(len("Location"), max(len(r[2]) for r in rows))
    head = "Harness".ljust(name_w) + "  " + "Status".ljust(stat_w) + "  Location"
    out = ["", "  " + fg(head, TEXT_RGB),
           "  " + fg("─" * len(head), HAIRLINE_RGB)]
    for name, status, path in rows:
        mark, color = marks.get(status, ("·", MUTED_RGB))
        out.append("  " + fg(name.ljust(name_w) + "  ", TEXT_RGB)
                   + fg(f"{mark} {status}".ljust(stat_w + 2), color)
                   + "  " + fg(path.ljust(loc_w), MUTED_RGB))
    return "\n".join(out)
