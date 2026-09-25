# Harness matrix

Verified against each harness's own documentation. Anything marked *unverified*
is deliberately not relied on by the installer.

## Skill discovery paths

| Harness | User | Project | Notes |
|---|---|---|---|
| Claude Code | `~/.claude/skills/<name>/SKILL.md` | `.claude/skills/`, plugin `<plugin>/skills/` | Does **not** read `.agents/skills/` ([issue #66352](https://github.com/anthropics/claude-code/issues/66352), closed not-planned). [docs](https://code.claude.com/docs/en/skills) |
| Codex CLI | `~/.agents/skills` | `$CWD/.agents/skills`, `$REPO_ROOT/.agents/skills` | `~/.codex/skills` appears in third-party writeups only — unverified. [docs](https://learn.chatgpt.com/docs/build-skills.md) |
| Gemini CLI | `~/.gemini/skills/`, `~/.agents/skills/` | `.gemini/skills/`, `.agents/skills/` | Tiers: builtin < extension < user < workspace. [docs](https://geminicli.com/docs/cli/skills/) |
| Cursor | `~/.cursor/skills/`, `~/.agents/skills/` | `.cursor/skills/`, `.agents/skills/` | Also reads `.claude/skills/` and `.codex/skills/` for compat; `.cursor/` wins conflicts. [docs](https://cursor.com/docs/skills) |
| OpenCode | `~/.config/opencode/skills/` | `.opencode/skills/`, `.claude/skills/`, `.agents/skills/` | Native `skill` tool loads bodies on demand. [docs](https://opencode.ai/docs/skills/) |
| OpenClaw | `~/.agents/skills`, `~/.openclaw/skills` | `<workspace>/skills`, `<workspace>/.agents/skills` | Chat-assistant gateway, not a coding harness; discovers any `SKILL.md` ≤6 levels deep. [docs](https://docs.openclaw.ai/tools/skills) |

`~/.agents/skills` is the closest thing to a shared location: read by Codex,
Gemini, Cursor, OpenCode and OpenClaw — but not Claude Code. Hence the
installer writes both.

## Per-prompt context injection

| Harness | Event | Config | Injects context? |
|---|---|---|---|
| Claude Code | `UserPromptSubmit` | `~/.claude/settings.json` (`hooks`) | Yes — stdout, or `hookSpecificOutput.additionalContext`. [docs](https://code.claude.com/docs/en/hooks) |
| Codex CLI | `UserPromptSubmit` | `~/.codex/hooks.json`, `[hooks]` in `config.toml` | Yes — `additionalContext`, default cap ~2500 tokens; needs `[features] hooks=true`. [docs](https://learn.chatgpt.com/docs/hooks) |
| Gemini CLI | `BeforeAgent` | `settings.json` (`hooks`) | Yes — `hookSpecificOutput.additionalContext`, that turn only. [docs](https://geminicli.com/docs/hooks/reference/) |
| Cursor | `beforeSubmitPrompt` | `.cursor/hooks.json`, `~/.cursor/hooks.json` | **No** — output is `{continue, user_message}` only. Injection exists on `sessionStart` (`additional_context`) and `postToolUse`. [docs](https://cursor.com/docs/agent/hooks) |
| OpenCode | `chat.message` plugin hook | `.opencode/plugins/`, `~/.config/opencode/plugins/` | Yes, via a TS plugin mutating `output.parts` — API surfaced in plugin types, not the docs page. Not wired by the installer. |
| OpenClaw | `agent:bootstrap` | `openclaw.json` `hooks.internal.*` | Context-mutating, but per-turn cadence unverified. [docs](https://docs.openclaw.ai/automation/hooks) |

## MCP server config

| Harness | File | Key | Shape |
|---|---|---|---|
| Claude Code | `~/.claude.json` (also nested per project), `.mcp.json` | `mcpServers` | JSON, `command`/`args`/`env` or `url` |
| Codex CLI | `~/.codex/config.toml`, `.codex/config.toml` | `[mcp_servers.<name>]` | TOML, the only underscore key |
| Gemini CLI | `~/.gemini/settings.json`, `.gemini/settings.json` | top-level `mcpServers` | JSON |
| Cursor | `~/.cursor/mcp.json`, `.cursor/mcp.json` | `mcpServers` | JSON |
| OpenCode | `opencode.json`, `~/.config/opencode/opencode.json` | `mcp` | JSON, `command` is an **array**, `type: local\|remote` |
| OpenClaw | `~/.openclaw/openclaw.json` | `mcp.servers` | JSON5 — the indexer skips it if strict JSON parsing fails |

## Frontmatter portability

Only `name` and `description` are honored everywhere. The
[agentskills.io spec](https://agentskills.io/specification) requires both:
`name` ≤64 chars, lowercase `a-z0-9-`, no leading/trailing or doubled hyphen,
matching the parent directory; `description` ≤1024 chars. `license`,
`compatibility` and `metadata` (string→string) are optional. `allowed-tools` is
experimental and ignored by several harnesses — this skill omits it.

Claude Code accepts extra keys (`when_to_use`, `disable-model-invocation`,
`model`, …) but Anthropic's own packaging path hard-errors on unknown keys, so
this skill ships spec-only frontmatter.

## Built-in tool search

Claude Code has `ToolSearch` (default on since v2.1.212; `ENABLE_TOOL_SEARCH=false`
disables) for deferred MCP tool schemas — the router names servers and leaves
schema loading to it. No other harness has an equivalent; they offer static
allow/deny filters only (`enabled_tools`/`disabled_tools` in Codex,
`includeTools`/`excludeTools` in Gemini, `tools` globs in OpenCode,
`tools.allow`/`deny` in OpenClaw, UI toggles in Cursor).
