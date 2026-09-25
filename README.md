# tool-router

Pick the right skills, MCP servers, subagents and commands *before* doing the
work — from a scored index of what is actually installed, not from memory.

Built for the case where a harness has hundreds of skills installed. The model
is supposed to see one description line each — but that listing is *budgeted*.
Claude Code allots ~1% of the context window (8,000 chars by default) to the
whole skill listing; on a box with 400 skills the **names alone** consume 7,897
of those characters, so nearly every description is dropped and native
selection degrades to name matching. (Check yours with `/context` and
`/skill-doctor`; raise it with `skillListingBudgetFraction` or
`SLASH_COMMAND_TOOL_CHAR_BUDGET` — a full 400-skill listing would need ~35k
tokens, which is why the budget exists.)

`tool-router` reads the descriptions off disk instead, where nothing is
truncated: it indexes every capability, scores them against the request and the
repo's stack, and hands the model a short routing card — what to load, what the
request actually asks for, and what to confirm before running.

Pure stdlib Python 3.9+. No API key, no embedding model, no daemon.
~3 ms per request over 500 capabilities.

## Install

```bash
git clone https://github.com/notjitin/tool-router ~/tool-router
python3 ~/tool-router/install.py --check     # see what's detected
python3 ~/tool-router/install.py             # install into every harness found
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
| OpenCode | `~/.config/opencode/skills/` | skill protocol (a `chat.message` plugin could automate it) |
| OpenClaw | `~/.openclaw/skills/` | skill protocol |

Paths and hook contracts: [`references/harnesses.md`](references/harnesses.md).

## Use

With a hook installed, nothing to do — a `## Router card` block appears in the
turn and the model follows it. Anywhere else, or to route a rephrased request:

```bash
~/.tool-router/route "the postgres query on the dashboard is slow"
~/.tool-router/index --cwd . --list      # rebuild + show the inventory
~/.tool-router/route --record tdd        # remember what actually helped here
~/.tool-router/route --selftest          # 17 assertions
```

Example card:

```
## Router card (tool-router)

**Step 1 — enrich.** Restate the request in 1-3 lines before acting: goal,
target, done-condition. ...
- intent: perf, data
- gaps: pronoun reference — resolve what 'it/this' points to

**Step 2 — load.** Invoke these with the Skill tool BEFORE any Edit/Write/...
- `sql-optimization-patterns` — Skill (score 1.42, matched: postgres,quer,slow)
- `postgres-code-review` — Skill (score 0.94, matched: postgres,quer)

_indexed: 33 agent, 30 command, 10 mcp, 59 plugin-skill, 392 skill_
```

## The pipeline (v2)

With a hook installed, every prompt runs four stages automatically:

1. **Route** the raw prompt — top-10 combined picks (skills, MCPs, subagents,
   commands; BM25 + semantic embeddings fused, Laya rerank with margin gating).
2. **Prompt-engineer rewrite** — an LLM restates the request as
   Goal / Target / Done-when / Steps / Verify (never adds scope). Providers,
   in order: Gemini (`GOOGLE_API_KEY`), then any OpenAI-compatible endpoint via
   `ROUTER_REWRITE_BASE_URL` / `ROUTER_REWRITE_API_KEY` / `ROUTER_REWRITE_MODEL`.
   Every failure is fail-open: the original prompt just routes alone.
3. **Re-route** the rewritten prompt.
4. **Merge + coverage** — union of both pick-sets plus a coverage line naming
   which capability kinds matched, so the agent can say when something is
   missing instead of silently substituting.

A number in the prompt ("top 20 tools") replaces the default 10, clamped 1–50.

## Configure

`~/Work/tool-router/config.json` (all keys optional):

```json
{
  "max_skills": 4,
  "min_score": 0.28,
  "tail_ratio": 0.55,
  "top_n": 10,
  "stale_hours": 24,
  "enabled": true,
  "mcp_hints": {"my-server": "words that should surface this server"},
  "rewriter": {"enabled": true, "timeout_s": 12.0, "min_chars": 12, "max_chars": 4000},
  "laya_rerank": {"enabled": true, "top_n": 5, "timeout_s": 0.6}
}
```

`min_score` is judged on one consistent scale after fusion (the old code
rescaled RRF scores below the floor, silently discarding semantic-only hits —
fixed; a regression test guards it). Breaker state lives in
`~/.tool-router/breaker-{laya,ollama,rewriter}.json`; deleting a file clears
that breaker. `enabled: false` silences the router without uninstalling it.

## Honest limits

- A hook can inject context; it cannot make the model load a skill, and there
  is no way to rewrite the user's prompt (no `updatedPrompt` exists —
  [#27365](https://github.com/anthropics/claude-code/issues/27365)). The card is
  advice plus evidence; `--enforce` is the only teeth available.
- Matching is lexical (BM25 + stemming + hint lists). A request sharing no
  vocabulary with a skill's description can still miss it. Research on
  skill retrieval at scale ([SkillRouter](https://arxiv.org/abs/2603.22455))
  finds the skill *body* is the decisive signal — indexing bodies with an
  embedding model is the upgrade path if description-level routing plateaus.
- Only names and descriptions are indexed, never skill bodies.
- Injected context accumulates for the whole session and is never freed
  ([#40216](https://github.com/anthropics/claude-code/issues/40216), closed as
  not planned). Hence the ~900-char card, the sub-threshold silence, and the
  160-char repeat form.
- Cursor cannot inject context per prompt; there the protocol lands once per
  session and the agent calls `route` itself. `additionalContext` is also
  reported not to reach the model in the VS Code extension
  ([#49063](https://github.com/anthropics/claude-code/issues/49063)).
- Nothing is logged. The router reads prompts and writes only the picked skill
  names to `~/.tool-router/last_route.json` — no prompt text leaves the machine
  or hits disk.

## Layout

```
SKILL.md                  the protocol the model follows
install.py                harness detection, skill install, hook wiring
scripts/router_core.py    discovery, indexing, scoring, card rendering
scripts/index_build.py    build ~/.tool-router/index.json
scripts/route.py          hook entry point and CLI
scripts/pipeline.py       4-stage orchestrator (route -> rewrite -> reroute -> merge)
scripts/rewriter.py       prompt-engineer stage (Gemini / OpenAI-compatible, fail-open)
scripts/session_card.py   session-start fallback (Cursor)
scripts/gate.py           optional PreToolUse enforcement (--enforce)
scripts/selftest.py       runnable checks
references/harnesses.md   verified per-harness paths, hooks, MCP formats
```

MIT.
