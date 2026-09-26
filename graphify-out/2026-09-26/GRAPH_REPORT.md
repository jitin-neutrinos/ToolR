# Graph Report - tool-router  (2026-09-26)

## Corpus Check
- 17 files · ~17,616 words
- Verdict: corpus is large enough that graph structure adds value.
- Unclassified: 1 file(s) not represented in the graph (top: (none) 1)

## Summary
- 233 nodes · 456 edges · 9 communities
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 9 edges (avg confidence: 0.89)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `5618c280`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- router_core.py
- selftest.py
- tool-router
- dense_index.py
- install.py
- route.py
- laya_rerank.py
- run
- tool-router — Extensive Review (2026-09-25)

## God Nodes (most connected - your core abstractions)
1. `tool-router` - 20 edges
2. `tool-router — Extensive Review (2026-09-25)` - 16 edges
3. `card_for()` - 15 edges
4. `score()` - 15 edges
5. `build_index()` - 13 edges
6. `run()` - 12 edges
7. `home()` - 10 edges
8. `tokenize()` - 9 edges
9. `_index()` - 9 edges
10. `get_index()` - 8 edges

## Surprising Connections (you probably didn't know these)
- `P2 — Detached-rebuild stampede (tiny)` --references--> `get_index()`  [INFERRED]
  REVIEW-2026-09-25.md → scripts/route.py
- `P1 — The newest, most complex code path has zero test coverage` --references--> `card_for()`  [INFERRED]
  REVIEW-2026-09-25.md → scripts/route.py
- `P0 — Score-scale collapse: dense-only hits can never make the card` --references--> `select()`  [INFERRED]
  REVIEW-2026-09-25.md → scripts/router_core.py
- `P0 — Straggler rows bypass fusion on a different scale` --references--> `select()`  [INFERRED]
  REVIEW-2026-09-25.md → scripts/router_core.py
- `tool-router` --implements--> `Codex CLI`  [EXTRACTED]
  README.md → references/harnesses.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Context Injection Across Harnesses** — claude_code, codex_cli, gemini_cli, cursor, opencode, openclaw, userpromptsubmit_event, beforeagent_event [EXTRACTED 0.85]
- **Hybrid Search Pipeline** — bm25, nomic_embed_text, laya_rerank, rrf_fusion [EXTRACTED 0.90]
- **Skill Metadata Specification** — skill_md_protocol, tool_router, index_json [EXTRACTED 0.90]

## Communities (9 total, 0 thin omitted)

### Community 0 - "router_core.py"
Cohesion: 0.11
Nodes (33): math, main(), build_index(), _counts(), _coverage_lines(), _dep_tokens(), discover_mcp(), discover_md_assets() (+25 more)

### Community 1 - "selftest.py"
Cohesion: 0.10
Nodes (27): langs_of(), BM25 over name+description, with field boost, phrase bonus, stack and history…, Deterministic pre-analysis handed to the model for its enrichment pass. This…, render_card(), scaffold(), score(), _index(), Frameworks live in dependency manifests, not always in config files. (+19 more)

### Community 2 - "tool-router"
Cohesion: 0.08
Nodes (28): BeforeAgent Event, BM25 Search Algorithm, Claude Code, Codex CLI, ColBERT Algorithm, Config JSON File, Cross-Encoder Reranker, Cursor (+20 more)

### Community 3 - "dense_index.py"
Cohesion: 0.23
Nodes (13): hashlib, _breaker_active(), build_dense(), dense_rank(), _embed(), item_hash(), Path, Return item content-hashes ranked by cosine to the prompt. [] on failure. (+5 more)

### Community 4 - "install.py"
Cohesion: 0.25
Nodes (21): _already(), detect(), home(), _hook_entry(), install_launchers(), install_skill(), load_json(), main() (+13 more)

### Community 5 - "route.py"
Cohesion: 0.16
Nodes (18): P1 — The newest, most complex code path has zero test coverage, P2 — Detached-rebuild stampede (tiny), card_for(), get_index(), _load_state(), main(), _prompt_key(), Path (+10 more)

### Community 6 - "laya_rerank.py"
Cohesion: 0.08
Nodes (35): argparse, json, os, pathlib, allow(), main(), Optional enforcement: make "load skills, then work" actually hold. A prompt…, Say nothing: an empty response leaves the normal permission flow alone. (+27 more)

### Community 7 - "run"
Cohesion: 0.11
Nodes (21): re, Full pipeline. Returns {card, picks, rewritten, n, provider}. rewrite=False…, pipeline.py — the full four-stage routing pipeline. Stage 1 route the user's…, The user's requested pick count, if the prompt names one., Top-N across all capability kinds: skills get priority of place, MCPs/…, _route_ctx(), _route_once(), run() (+13 more)

### Community 8 - "tool-router — Extensive Review (2026-09-25)"
Cohesion: 0.12
Nodes (16): Accuracy spot-check (12 queries, full pipeline), Enhancement shortlist (post-fix, ranked by value/effort), P0 — Repo has uncommitted work, P0 — Score-scale collapse: dense-only hits can never make the card, P0 — Straggler rows bypass fusion on a different scale, P1 — Circuit breakers are process-local, so they never persist, P1 — Laya rerank costs ~0.9–1.1 s on EVERY prompt, for override-able value, P2 — Dead config + doc drift (+8 more)

## Knowledge Gaps
- **26 isolated node(s):** `Verdict`, `P0 — Repo has uncommitted work`, `P1 — Laya rerank costs ~0.9–1.1 s on EVERY prompt, for override-able value`, `P1 — Circuit breakers are process-local, so they never persist`, `P2 — Enforcement gate is latent-broken (not currently wired, so dormant)` (+21 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 91 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `tool-router — Extensive Review (2026-09-25)` connect `tool-router — Extensive Review (2026-09-25)` to `route.py`?**
  _High betweenness centrality (0.089) - this node is a cross-community bridge._
- **Why does `select()` connect `tool-router — Extensive Review (2026-09-25)` to `router_core.py`, `selftest.py`, `route.py`, `run`?**
  _High betweenness centrality (0.069) - this node is a cross-community bridge._
- **Why does `card_for()` connect `route.py` to `tool-router — Extensive Review (2026-09-25)`, `selftest.py`, `router_core.py`, `run`?**
  _High betweenness centrality (0.050) - this node is a cross-community bridge._
- **What connects `Verdict`, `P0 — Repo has uncommitted work`, `P1 — Laya rerank costs ~0.9–1.1 s on EVERY prompt, for override-able value` to the rest of the system?**
  _26 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `router_core.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11428571428571428 - nodes in this community are weakly interconnected._
- **Should `selftest.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1028225806451613 - nodes in this community are weakly interconnected._
- **Should `tool-router` be split into smaller, more focused modules?**
  _Cohesion score 0.082010582010582 - nodes in this community are weakly interconnected._