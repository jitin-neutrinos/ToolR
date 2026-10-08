---
name: tool-router
description: Route each request to the right local capabilities before doing the work. Use at the start of a turn, or whenever choosing which skills, MCP servers, subagents, plugins or slash commands apply — especially with a large installed set where scanning descriptions by hand is unreliable. Indexes every skill, subagent, command and MCP server the harness can see, scores them against the request plus the repo's own stack, enriches the request into an explicit goal/target/done-condition, loads what scored, and flags destructive work before it runs. Triggers on "which skill should I use", "route this", "what tools apply", "load the right skills", "tool-router", or any specialized request in a repo with many installed skills.
license: MIT
metadata:
  version: "1.0.0"
  homepage: "https://github.com/jitin-neutrinos/Toutur"
---

# tool-router

Pick capabilities before doing work, from evidence instead of memory.

A harness shows the model a name and one description line per installed skill.
That is enough to pick from a dozen. At 300-500 skills — plus subagents,
commands, plugin skills and MCP servers — selection quietly degrades: the
plausible-sounding skill wins over the right one, and specialized skills that
took effort to install never fire. This skill replaces the guess with a scored,
inspectable answer computed from what is actually installed.

## The loop, once per request

**0. Index (once, then when skills change).** `~/.tool-router/index.json` holds
every capability the harness can see. Rebuild after installing or removing
skills:

```bash
~/.tool-router/index --cwd .        # add --list to see the inventory
```

**0b. The pipeline (automatic when the hook fires).** Every routed prompt runs
one stage, and it is fast enough to run again whenever you want:

1. **Route** — top-10 combined picks across skills, MCPs, subagents, commands
   and plugin skills. BM25 + local-embedding fusion (Ollama nomic-embed-text)
   plus a capability-alias layer that bridges plain English to library names, so
   "animate" reaches the Framer skills and "frosted" reaches glassmorphism.
   The alias layer exists because the measured failure mode was vocabulary, not
   ranking: 64% of real prompts shared zero tokens with their own correct
   capability.

Then a **coverage** line naming which capability kinds were found, and the
load/gate contract.

The prompt-engineer rewrite stage was **removed 2026-10-05**. It made a network
call on every single message, the free tiers it used returned HTTP 200 with an
empty body about one call in five, and the card's pick list already names the
capabilities to load — so it cost ~2.5-5.5 s (15-18 s outliers) for nothing.
`--no-rewrite` is still accepted and now does nothing.

**1. Route.** With the prompt-submit hook installed (Claude Code, Codex CLI,
Gemini CLI, Hermes gateway) the card arrives on its own — you will see a
`## Router card` block in the turn's context. Without a hook, or any time you
want a card for a reformulated request, ask for one:

```bash
~/.tool-router/route --top 10 "the user's request, verbatim"
```

**That is the entire interface.** `--top N` is optional (default 10, max 50).
A number written inside the request ("top 20 skills for…") is honoured too, and
the flag wins when both are present. A bad flag prints a worked example rather
than a usage wall; `--help` lists everything. Cost is ~0.2 s with no model call
and no network, so there is no reason to avoid calling it twice.

When the user says "use the tool router", run the command. Do not re-derive the
pick list by hand, and do not guess at flags.

Empty output is an answer: nothing specialized applies. Proceed unaided. Cards
are suppressed for machine-generated submissions (scheduled and loop wakeups,
task notifications, slash-command relays) and for any prompt the user prefixes
with `*` or `#` — that prefix is the deliberate "no routing this turn" bypass.
When the route is unchanged from the previous turn, the card shrinks to one
line naming it, because injected context is never freed.

**2. Enrich.** Before the first tool call, restate the request in one to three
lines: **goal**, **target**, **done-condition**. The card supplies the raw
signals — detected intents, named files, explicit constraints, and the gaps it
found (missing error text, unresolved "it", no named target).

Two rules make enrichment safe:

- Assumptions get stated as assumptions. "Assuming you mean the Postgres
  adapter, not the SQLite one — say otherwise and I'll switch."
- A gap that changes what you would build is a question, not a guess. A gap
  that only changes a detail is an assumption you name and move past.

Never silently replace the user's request with your reformulation, and never
add scope they did not ask for. Enrichment sharpens; it does not expand.

**3. Load.** Invoke the skills the card names, highest score first, *before*
any Edit/Write or state-changing Bash. The card gives each pick's score and the
tokens it matched on, so a wrong pick is visible: skip it and say why in one
line. Also on the card when they match: MCP servers (the doc, browser, or
issue-tracker server that belongs in this turn), subagents worth delegating to,
and slash commands that already do the task.

Nothing here overrides the harness. On Claude Code, MCP tool schemas still load
through `ToolSearch`; the card tells you *which* server matters, then you fetch
its schema the normal way.

**4. Gate.** Risk flags on the card (`destructive`, `secrets`, `outbound`,
`migration`) mean: state exactly what will change, in plain language, and get
confirmation before running it. A flag is not a veto — it is a checkpoint that
must be spoken out loud rather than assumed.

**5. Record (optional).** Teach the router what actually worked here:

```bash
~/.tool-router/route --record tdd,python-testing-patterns
```

Recorded names get a small ranking nudge in later turns, capped so history can
never outweigh a genuine match.

## How the ranking works

BM25 over each capability's name and description, then five adjustments, each
there to kill a specific failure:

| Adjustment | Fixes |
|---|---|
| Name-field weight ×2.5 | A hit in a skill's own name beats one buried in prose |
| Generic-token damping ×0.4 | Stops a skill named `configure` winning "how do I configure Next.js" |
| Ecosystem gate ×0.3 | Stops `golang-testing` answering a pytest question |
| Repo-stack boost | `mix.exs` present tips a tie from `react-specialist` to `liveview` |
| Exact-name mention +1.0 | "use the tdd skill" wins outright; a bare generic verb does not |

Scores are relative to the request's own weight, so the confidence threshold
means the same thing whether 20 or 500 skills are installed. Everything is
lexical and local: no API call, no embedding model, ~3ms per request over 500
capabilities. That is also the honest limit — see below.

## Config

`~/.tool-router/config.json`, all optional:

```json
{
  "max_skills": 4,
  "min_score": 0.28,
  "tail_ratio": 0.55,
  "stale_hours": 24,
  "enabled": true,
  "mcp_hints": {"my-server": "words that should surface this server"}
}
```

`min_score` is the confidence floor — raise it toward 0.4 for fewer, surer
picks; lower it toward 0.2 to see more candidates. `enabled: false` silences the
router without uninstalling it. `mcp_hints` exists because no MCP config format
carries a description; the shipped defaults cover common servers.

## Sourcing — the skill finder

When no local capability serves a need, two lanes find one on the free
registries (skills.sh, GitHub, official MCP registry). Nothing is ever
installed without approval except by the auto lane's hardened bar.

- **Interactive (HITL, the default):** `~/.tool-router/route --finder "<need>"`
  searches, then inspects each candidate's REAL SKILL.md body + scripts,
  injection-screens (Laya) the full text, scans destructive IOCs
  (curl|sh, base64-decode, credential paths, exfil hosts, raw IPs,
  persistence), and flags near-name publishers (typosquat). Candidates show
  commit SHAs. Install with `route --finder-install <n>` — pinned to the
  reviewed commit, refused if the repo moved since review — and the loop
  re-routes the original prompt to prove the new skill actually fires
  ("installed but never fires" is the #1 post-install failure). MCP servers
  are installable candidates but always HITL, never auto.
- **Auto (repeated gaps only):** after the SAME intent gap repeats ≥3 times
  AND the gap oracle confirms nothing local serves it, a candidate may
  install unattended — but only a skill that is free, ≥1000 installs,
  relevance-judged (gemini-flash), body-inspected, screened "safe" on
  body+scripts, IOC-clean, and not a typosquat. Any veto degrades to HITL;
  every veto and install lands in `sourcing.log`.

Why the body screen: the 2026 marketplace audits (Koi ClawHavoc, Snyk
ToxicSkills, Unit 42) showed semantic attacks hide agent-directed
instructions in SKILL.md bodies that code scanners read as documentation
(0% detection). Install counts and listing trust are not safety.

## What this does not do

- **It cannot force a skill to load.** No harness hook can invoke a skill or
  rewrite the prompt — the only outputs are injected context and a block. The
  card is a strong instruction with visible evidence, not enforcement. The one
  real lever is `install.py --enforce` (Claude Code): a `PreToolUse` deny that
  refuses the *first* Edit/Write of a request when the routed skills were never
  loaded, once per request so it cannot loop.
- **Lexical matching has a ceiling.** It handles synonyms only through stemming
  and the hint lists, so a request phrased with no shared vocabulary
  ("make the page feel snappier") may miss a skill that would have helped. Fix
  such misses in the skill's own description, or with a `mcp_hints`-style hint —
  a skill whose description states when to use it routes well everywhere,
  including in the harness's native selection.
- **It reads descriptions, not skill bodies.** Two skills with near-identical
  descriptions will look near-identical to the router.
- **Cursor gets a weaker version.** Its prompt hook cannot inject context, so
  there the protocol arrives once at session start and you run `route.py`
  yourself.

## Runnable check

```bash
~/.tool-router/route --selftest
```

Thirteen assertions over frontmatter parsing, tokenizing, ranking, the ecosystem
gate, generic damping, dependency-manifest stack detection, machine-prompt
filtering, repeat compaction, card framing, and live routing latency.
Run it after editing anything in `scripts/`.
