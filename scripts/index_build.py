#!/usr/bin/env python3
"""Build the tool-router index of skills, subagents, commands, plugins and MCP servers.

    python3 index_build.py            # index for the current directory
    python3 index_build.py --cwd DIR  # index as if running in DIR
    python3 index_build.py --list      # show what was found
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cwd", default=".")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cwd = Path(args.cwd).resolve()
    t0 = time.time()
    index = rc.build_index(cwd)
    path = rc.save_index(index)
    ms = (time.time() - t0) * 1000

    # dense lane (best-effort): embed items for hybrid retrieval. Ollama down ->
    # skipped silently; router stays BM25-only. Only new/changed items embedded.
    dense_stats = ""
    try:
        import dense_index as di
        dense_path = path.parent / (path.stem + ".dense.npz")
        dstats = di.build_dense(index, dense_path)
        if dstats.get("ok"):
            dense_stats = (f", dense: {dstats['embedded']} embedded, "
                           f"{dstats['reused']} reused")
    except Exception:
        pass

    if args.list:
        for it in sorted(index["items"], key=lambda i: (i["kind"], i["name"])):
            print(f"{it['kind']:<14} {it['name']:<40} {it['scope']:<18} {it['desc'][:60]}")
    if not args.quiet:
        stats = ", ".join(f"{v} {k}" for k, v in sorted(index["stats"].items()))
        print(f"indexed {stats} in {ms:.0f}ms -> {path}{dense_stats}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
