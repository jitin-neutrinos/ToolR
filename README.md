# Toutur

<p align="center">
  <img src="assets/toutur-wordmark.png" alt="Toutur wordmark" width="480">
</p>

Pick the right skills, MCP servers, subagents and commands *before* doing the
work — from a scored index of what is actually installed, not from memory.

## Why

Every harness has a skill-discovery budget. Claude Code allots ~1% of the
context window to the whole skill listing; past a few dozen skills the
descriptions are truncated or dropped (least-invoked first), selection degrades
to name matching, and studies show pass rates falling −8…−21pp as the skill
count grows past ~50. The same trap exists for MCP tools: accuracy collapses
past 30–50 visible tools, and Anthropic's own tool search returns only ~34–56%
retrieval accuracy in independent tests at scale.

Toutur reads the descriptions off disk instead, where nothing is truncated. It
indexes every capability, scores them against the request (BM25 + local
embeddings fused, plus a capability-alias vocabulary bridge), and hands the
model a short **routing card** — what to load, what the request actually asks
for, and what to confirm before running. It runs on every prompt, in ~0.2 s,
with no API calls on the hot path.

**Measured on its own 454-query golden set:** recall@1 0.18, recall@10 0.44,
MRR 0.27 — and every change is gated on that set before it ships (see
`eval/`).

## How it compares

| | **Toutur** | skill-search-mcp | Skill Context Manager | Native tool search (Claude/Codex) |
|---|---|---|---|---|
| Harnesses | **6+ (Claude, Codex, Gemini, Cursor, OpenCode, OpenClaw, ~/.agents)** | Claude Code | One harness | One harness |
| Routes… | **skills + MCPs + subagents + commands** | skills only | skills only | MCP tools only |
| Method | **BM25 + dense embeddings (convex fusion) + alias bridge** | vector search | BM25 + embeddings + reranker | regex / BM25 |
| Vocabulary bridging | **yes — plain English → library names** ("frosted" → glassmorphism) | implicit in vectors | partial | none |
| Body + trigger indexing | **yes** (skill bodies, Use-when clauses, trigger phrases) | descriptions only | descriptions only | tool descriptions only |
| Install surface | **one curl\|sh line; hook-native, no MCP registration** | pipx + claude mcp add + reindex | MCP server | built-in |
| Card, not just picks | **yes — restate/load/gate contract** | tool list | tool list | schemas |
| Sourcing stage (find missing capabilities, HITL) | **yes** | no | no | no |
| Eval harness included | **yes (golden set + ablation + robustness)** | benchmark numbers | no | vendor evals |

Native tool search is complementary, not a replacement: it hides MCP tool
schemas; Toutur decides what the agent should *load* across all capability kinds.

## Install

Landing page with full architecture diagrams and per-OS instructions:
**https://toutur.jitinnair.com/landing/**

```bash
curl -fsSL https://toutur.jitinnair.com/install.sh | bash
```

Windows (PowerShell 5.1+):

```powershell
irm https://toutur.jitinnair.com/install.ps1 | iex
```

The installer detects every harness on the machine — Claude Code, Codex CLI,
Gemini CLI, Antigravity, Cursor, OpenCode, OpenClaw, and the shared
`~/.agents` location, even when only their config directories exist — and
installs into all of them: skill, prompt hook, and the "route before you work"
mandate. A step-by-step TUI shows exactly what it found and changed.

Manual install:

```bash
git clone https://github.com/jitin-neutrinos/Toutur ~/toutur
python3 ~/toutur/install.py --check       # see what's detected, change nothing
python3 ~/toutur/install.py               # install into every harness found
python3 ~/toutur/toolr_install.py --demo  # preview the TUI, change nothing
```

`--check` changes nothing. A real install symlinks the skill into each detected
harness's skill directory, wires a prompt hook where the harness supports one,
writes `~/.tool-router/{route,index}` launchers, and builds the index. Settings
files are backed up as `*.tool-router.bak` before the first edit, and re-running
is a no-op.

Useful flags: `--harness claude` (one only), `--project .` (also install into
this repo), `--copy` (copy instead of symlink), `--no-hooks`, `--uninstall`,
and `--enforce` (Claude Code only — see *Enforcement* below).

### Enforcement (opt-in)

A prompt hook can only advise. `--enforce` adds the one binding mechanism a
harness offers: a `PreToolUse` deny on `Edit|Write|MultiEdit|NotebookEdit` that
refuses the **first** such call of a request when the routed skills were never
loaded, plus a `PostToolUse(Skill)` hook that clears the flag. It fires at most
once per request — repeated refusal is how enforcement hooks become loops — so
after one nudge the decision is the model's again. No `Stop` hook is installed;
that shape is the documented infinite-loop trap.

| Harness | Skill install | Per-prompt card |
|---|---|---|
| Claude Code | `~/.claude/skills/` | automatic (`UserPromptSubmit`) |
| Codex CLI | `~/.agents/skills/` | automatic (`UserPromptSubmit`, needs `[features] hooks=true`) |
| Gemini CLI | `~/.gemini/skills/` | automatic (`BeforeAgent`) |
| Cursor | `~/.cursor/skills/` | session-start protocol; agent runs `route` per turn |
| OpenCode | `~/.config/opencode/skills/` | automatic (`chat.message` plugin) |
| Antigravity (agy) | `~/.gemini/config/skills/` | skill protocol (no per-prompt hook exists) |
| OpenClaw | `~/.openclaw/skills/` | skill protocol |

Paths and hook contracts: [`references/harnesses.md`](references/harnesses.md).

## Use

With a hook installed, nothing to do — a `## Router card` block appears in the
turn and the model follows it. Anywhere else, or to route a rephrased request:

```bash
~/.tool-router/route "the postgres query on the dashboard is slow"
~/.tool-router/index --cwd . --list     # rebuild + show the inventory
~/.tool-router/route --record tdd       # remember what actually helped here
~/.tool-router/route --selftest         # 43 checks
```

Example card:

```
## Router card (Toutur)

**Step 1 — enrich.** Restate the request in 1-3 lines before acting: goal,
target, done-condition. ...
- intent: perf, data
- gaps: pronoun reference — resolve what 'it/this' points to

**Step 2 — load.** Invoke these with the Skill tool BEFORE any Edit/Write/...
- `sql-optimization-patterns` — Skill (score 1.42, matched: postgres,quer,slow)
- `postgres-code-review` — Skill (score 0.94, matched: postgres,quer)

_indexed: 33 agent, 30 command, 10 mcp, 59 plugin-skill, 392 skill_
```

## The pipeline

```
prompt → BM25 over name+desc+aliases+bodies+triggers
       → dense verification lane (local Ollama, advisory)
       → convex score fusion (config fusion_alpha, default 0.5)
       → exact-name guarantee
       → top-10 combined card (~0.2 s, hook-native)
```

Fail-open everywhere: dense lane down → BM25 alone; sourcing sweep is a
detached background child that never blocks the card. Nothing is logged except
picked names; prompt text never leaves the machine or hits disk beyond the
router state dir.

A number in the prompt ("top 20 tools") or `-n/--top N` replaces the default
10, clamped 1–50; the flag wins when both are present.

## Configure

`~/.tool-router/config.json` (all keys optional):

```json
{
  "max_skills": 4,
  "min_score": 0.28,
  "tail_ratio": 0.55,
  "top_n": 10,
  "budget_ms": 5000,
  "stale_hours": 24,
  "enabled": true,
  "fusion_alpha": 0.5,
  "mcp_hints": {"my-server": "words that should surface this server"}
}
```

`fusion_alpha` is config-controlled and swept against the golden set
(`eval/ablate.py`); 0.5 shipped because it keeps recall@10 while 0.6 trades
R@10 −0.078 for R@1 +0.017. Breaker state lives in
`~/.tool-router/breaker-*.json`; deleting a file clears that breaker.
`enabled: false` silences the router without uninstalling it.

## Honest limits

- A hook can inject context; it cannot make the model load a skill, and there
  is no way to rewrite the user's prompt (no `updatedPrompt` exists —
  [#27365](https://github.com/anthropics/claude-code/issues/27365)). The card is
  advice plus evidence; `--enforce` is the only teeth available.
- Matching is hybrid, but a request sharing no vocabulary with any description
  can still miss. Extend `scripts/aliases.py` — it is the front line, and
  `coverage_report()` flags phantom keys (alias keys matching no live
  capability). Skill bodies are now indexed (first 120 words), following
  [SkillRouter](https://arxiv.org/abs/2603.22455), which found the body is the
  decisive signal; full-body indexing is the next lever if this plateaus.
- Injected context accumulates for the whole session and is never freed
  ([#40216](https://github.com/anthropics/claude-code/issues/40216), closed as
  not planned). Hence the compact repeat-card form.
- Cursor cannot inject context per prompt; there the protocol lands once per
  session and the agent calls `route` itself.
- The dense lane uses a local embedding model if Ollama is present; without it,
  BM25-only routing still works.

## Development

```bash
python3 scripts/selftest.py          # 43 checks
python3 eval/robustness.py           # 26 adversarial checks (5 known defects)
python3 eval/ablate.py               # lane-by-lane metrics on the golden set
python3 eval/ablate.py --save        # append to eval/runs.jsonl
python3 install.py --check           # detection report
python3 toolr_install.py --demo      # TUI preview, change nothing
```

Design notes and measured post-mortems: `RESEARCH-retrieval-quality-2026-10-05.md`,
`REVIEW-*.md`, `OPTIMIZATION-2026-10-06.md`.

## Layout

```
SKILL.md                  the protocol the model follows
install.py                harness detection, skill install, hook wiring
toolr_install.py          the branded TUI installer
toolr_tui.py              pixel-art logo + step renderer
assets/                   wordmark + icon (generated)
scripts/router_core.py    discovery, indexing, scoring, fusion, card rendering
scripts/body_extract.py   SKILL.md body/when/trigger extraction
scripts/route.py          hook entry point and CLI
scripts/pipeline.py       routing pipeline
scripts/aliases.py        capability-alias + concept-term vocabulary bridge
scripts/dense_index.py    local-embedding dense lane (Ollama nomic-embed-text)
scripts/source.py         registry sourcing (HITL)
scripts/session_card.py   session-start fallback (Cursor)
scripts/gate.py           optional PreToolUse enforcement (--enforce)
scripts/selftest.py       runnable checks
eval/                     golden set, ablation, robustness, active learning
references/harnesses.md   verified per-harness paths, hooks, MCP formats
```

MIT.
