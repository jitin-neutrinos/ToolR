#!/usr/bin/env python3
"""Self-check: tool-router hybrid Stage 1 (BM25 + dense RRF fusion).
Run: python3 test_hybrid.py   (needs Ollama up for the dense lane)
"""
import json, sys
sys.path.insert(0, "/home/notjitin/Work/tool-router/scripts")
from pathlib import Path
import dense_index as di

index = json.load(open("/home/notjitin/.tool-router/index.json"))
dense_path = Path("/home/notjitin/.tool-router/index.dense.npz")
assert dense_path.is_file(), "dense index missing - run index_build.py first"
meta = json.loads(dense_path.with_suffix(".meta.json").read_text())
assert len(meta["hashes"]) == len(index["items"]), "dense/meta misaligned with index"

h2n = {di.item_hash(it): it["name"] for it in index["items"]}

ranked = di.dense_rank("ecto elixir database migrations", index, dense_path, top_n=3)
names = [h2n.get(h) for h in ranked]
assert "apply-ecto-conventions" in names, f"semantic bridge failed: {names}"
print("hybrid dense-lane self-check: PASS", names)
