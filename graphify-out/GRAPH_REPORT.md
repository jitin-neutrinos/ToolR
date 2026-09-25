# Graph Report - tool-router  (2026-09-25)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 174 nodes · 350 edges · 8 communities
- Extraction: 98% EXTRACTED · 2% INFERRED · 0% AMBIGUOUS · INFERRED: 6 edges (avg confidence: 0.85)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- router_core.py
- selftest.py
- tool-router
- dense_index.py
- install.py
- route.py
- gate.py
- skip_reason

## God Nodes (most connected - your core abstractions)
1. `tool-router` - 20 edges
2. `card_for()` - 15 edges
3. `score()` - 14 edges
4. `build_index()` - 11 edges
5. `home()` - 10 edges
6. `tokenize()` - 9 edges
7. `_index()` - 9 edges
8. `index_path()` - 8 edges
9. `discover_plugin_assets()` - 7 edges
10. `discover_skills()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `tool-router` --implements--> `Codex CLI`  [EXTRACTED]
  README.md → references/harnesses.md
- `tool-router` --implements--> `Gemini CLI`  [EXTRACTED]
  README.md → references/harnesses.md
- `tool-router` --references--> `Ollama`  [EXTRACTED]
  README.md → research-stage1-verdict.md
- `tool-router` --implements--> `OpenClaw`  [EXTRACTED]
  README.md → references/harnesses.md
- `tool-router` --implements--> `OpenCode`  [EXTRACTED]
  README.md → references/harnesses.md

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **Context Injection Across Harnesses** — claude_code, codex_cli, gemini_cli, cursor, opencode, openclaw, userpromptsubmit_event, beforeagent_event [EXTRACTED 0.85]
- **Hybrid Search Pipeline** — bm25, nomic_embed_text, laya_rerank, rrf_fusion [EXTRACTED 0.90]
- **Skill Metadata Specification** — skill_md_protocol, tool_router, index_json [EXTRACTED 0.90]

## Communities (8 total, 0 thin omitted)

### Community 0 - "router_core.py"
Cohesion: 0.14
Nodes (29): math, re, main(), build_index(), _counts(), _dep_tokens(), discover_mcp(), discover_md_assets() (+21 more)

### Community 1 - "selftest.py"
Cohesion: 0.11
Nodes (27): langs_of(), BM25 over name+description, with field boost, phrase bonus, stack and history…, Deterministic pre-analysis handed to the model for its enrichment pass. This…, render_card(), scaffold(), score(), _index(), Frameworks live in dependency manifests, not always in config files. (+19 more)

### Community 2 - "tool-router"
Cohesion: 0.08
Nodes (28): BeforeAgent Event, BM25 Search Algorithm, Claude Code, Codex CLI, ColBERT Algorithm, Config JSON File, Cross-Encoder Reranker, Cursor (+20 more)

### Community 3 - "dense_index.py"
Cohesion: 0.13
Nodes (20): hashlib, json, build_dense(), dense_rank(), _embed(), item_hash(), Path, Return item content-hashes ranked by cosine to the prompt. [] on failure. (+12 more)

### Community 4 - "install.py"
Cohesion: 0.25
Nodes (21): _already(), detect(), home(), _hook_entry(), install_launchers(), install_skill(), load_json(), main() (+13 more)

### Community 5 - "route.py"
Cohesion: 0.16
Nodes (18): card_for(), get_index(), _load_state(), main(), Path, Route one prompt to the skills, subagents, commands and MCP servers that fit…, Refresh a stale index without making the user wait for it., _rebuild_detached() (+10 more)

### Community 6 - "gate.py"
Cohesion: 0.16
Nodes (14): argparse, os, pathlib, allow(), main(), Optional enforcement: make "load skills, then work" actually hold. A prompt…, Say nothing: an empty response leaves the normal permission flow alone., _read() (+6 more)

### Community 7 - "skip_reason"
Cohesion: 0.50
Nodes (4): Return why this submission should not be routed, or None to route it., skip_reason(), Machine-generated submissions and bypassed prompts must not be routed., t_skip_reason()

## Knowledge Gaps
- **15 isolated node(s):** `Config JSON File`, `Cursor`, `Index JSON File`, `Ollama`, `OpenClaw` (+10 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 62 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `score()` connect `selftest.py` to `router_core.py`, `route.py`?**
  _High betweenness centrality (0.042) - this node is a cross-community bridge._
- **Why does `card_for()` connect `route.py` to `router_core.py`, `selftest.py`?**
  _High betweenness centrality (0.032) - this node is a cross-community bridge._
- **What connects `Config JSON File`, `Cursor`, `Index JSON File` to the rest of the system?**
  _15 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `router_core.py` be split into smaller, more focused modules?**
  _Cohesion score 0.1425287356321839 - nodes in this community are weakly interconnected._
- **Should `selftest.py` be split into smaller, more focused modules?**
  _Cohesion score 0.11264367816091954 - nodes in this community are weakly interconnected._
- **Should `tool-router` be split into smaller, more focused modules?**
  _Cohesion score 0.082010582010582 - nodes in this community are weakly interconnected._
- **Should `dense_index.py` be split into smaller, more focused modules?**
  _Cohesion score 0.12554112554112554 - nodes in this community are weakly interconnected._