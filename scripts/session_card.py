#!/usr/bin/env python3
"""Session-level fallback for harnesses that cannot inject context per prompt.

Cursor's `beforeSubmitPrompt` hook can only allow or block a prompt, so there is
no way to hand it a per-prompt routing card. Its `sessionStart` hook *can* inject
text once — so inject the protocol instead of the card, and let the agent run
route.py itself at the start of each turn.

Emits Cursor's sessionStart shape (`additional_context`) plus the generic
`hookSpecificOutput` shape, so one script serves either contract.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402

ROUTE = Path(__file__).resolve().parent / "route.py"


def main() -> int:
    sys.stdin.read() if not sys.stdin.isatty() else ""  # drain payload, unused
    index = rc.load_index()
    stats = index.get("stats", {}) if index else {}
    counts = ", ".join(f"{v} {k}" for k, v in sorted(stats.items())) or "not built yet"
    cwd = os.getcwd()
    text = (
        "## tool-router protocol (this session)\n\n"
        f"Local capability index: {counts}.\n\n"
        "At the START of every turn, before any other tool call, run:\n\n"
        f"    python3 {ROUTE} \"<the user's request, verbatim>\"\n\n"
        "It prints a routing card: the enrichment checklist, the skills worth "
        "loading (highest score first), matching MCP servers and subagents, and "
        "any risk gates. Load the named skills, restate the request in 1-3 lines, "
        "then do the work. Empty output means nothing specialized applies — "
        "proceed normally.\n\n"
        f"Rebuild the index after installing skills: python3 "
        f"{ROUTE.parent / 'index_build.py'} --cwd {cwd}\n"
    )
    print(json.dumps({
        "continue": True,
        "additional_context": text,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text},
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
