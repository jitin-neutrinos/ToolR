# Full-coverage capability discovery for the local tool-router

Research date: 2026-10-05. Machine: kurama-core (Linux 7.2.4, Nobara 44, x86_64).
Every number below was produced on this box during this session; every URL was fetched
and its status recorded. Anything I did **not** retrieve is marked **UNVERIFIED**.

---

## 0. Headline findings (read these)

1. **The "stale skills are never removed" gap in the briefing is wrong.** `build_index()` is a
   full rediscovery each run; a deleted skill disappears immediately. I proved it: created
   `~/.agents/skills/__probe_skill__` → 763 items; deleted it → 762 items, and the dense
   meta file shrank to 762 hashes. The real gap is **staleness in the sense of never noticing
   that a capability no longer works** (a skill whose repo died, an MCP that no longer boots),
   not stale *rows*.
2. **The "nothing watches the filesystem" gap is also wrong** — but the watcher it refers to
   is **broken and has been dead since 2026-10-01 17:17** (`unit-start-limit-hit`). Worse, it
   only watches `%h/.hermes/skills` at the top level, so a skill added inside a *category*
   subdir (`~/.hermes/skills/devops/x/`) fires **nothing**. Verified by experiment: nested
   create → no journal entry; top-level create → rebuild fired. I reset-failed and restarted it
   during research (it is active again) — treat that as a live fix, see §7.
3. **The biggest real coverage hole is MCP tool-level detail.** The router indexes 53 MCP
   servers by *name* only. A live `list_tools()` sweep found **484 distinct tools across 35
   reachable servers, and 465 of them (96%) appear nowhere in the index**. Server names are
   indexed; what each server *exposes* is invisible to retrieval.
4. **Corpus is one namespace, not eight.** `~/.hermes/skills`, `~/.claude/skills`,
   `~/.gemini/config/skills`, `~/.config/opencode/skills` each contain the **identical**
   540 skill names. Index reports `harness=claude` for 655/762 items because dedupe is
   first-wins by `SKILL_ROOTS` order. The corpus is effectively ~540 unique skills, not 555+62.
5. **Registrates**: the official MCP registry, skills.sh, clawhub, smithery, lobehub,
   browse.sh, HF Spaces all have working unauthenticated JSON APIs (all verified 200,
   burst-tested). **Glama, PulseMCP, Composio, kiprio, mcp.so, mcpservers.org have no usable
   free programmatic search** — Glama's `/v1/servers` now returns 401 requiring an API key;
   PulseMCP's `v0beta` is sunset (410) and `v0.1` needs `X-API-Key`; mcp.so and
   mcpservers.org both answer JSON requests with `500 {"error":"Only HTML requests are
   supported here"}` and their `robots.txt` disallows `/api/` and `/search`.

---

## 1. Taxonomy of capability sources, with discovery surfaces

Measured counts are this machine's. "Parse without running?" = can you get metadata from
files/API alone, with no tool execution.

### 1a. Agent skills — every known layout

`SKILL.md` + YAML frontmatter is the only universal contract. Spec verified at
`https://agentskills.io/specification`: required `name` (≤64 chars) and `description`
(≤1024); optional `license`, `compatibility`, `metadata`, `allowed-tools`. Frontmatter-only
parsing means **all of this is parseable without running the tool** (already true in
`parse_frontmatter`).

| Layout | Path on this box | # SKILL.md | Parse w/o running | Cheap drift signal |
|---|---|---|---|---|
| Hermes user | `~/.hermes/skills/<cat>/<name>/SKILL.md` | 519 (+22 top-level) | yes | mtime+size, `.hub/lock.json` hash |
| Claude | `~/.claude/skills/<name>/SKILL.md` | 540 | yes | mtime+size |
| Gemini CLI / agy | `~/.gemini/config/skills/<name>/` | 540 | yes | mtime+size |
| Antigravity builtin | `~/.gemini/antigravity-cli/builtin/skills` | 9 | yes | mtime+size |
| Cursor | `~/.cursor/skills/` | 7 | yes | mtime+size |
| OpenCode | `~/.config/opencode/skills/<cat>/<name>/` | 540 | yes | mtime+size |
| Canonical `.agents` | `~/.agents/skills/` | 13 | yes | mtime+size |
| Plugins (Claude) | `~/.claude/plugins/cache/*/*/*/skills/*/SKILL.md` | 62 indexed | yes | `installed_plugins.json` version, marketplace git SHA |
| Plugins (flat) | `~/.config/opencode/plugins`, `~/.gemini/config/plugins` | 0 here | yes | same |
| Remote `.well-known` | `https://<host>/.well-known/skills/index.json` | n/a | yes (HTTP GET) | HTTP ETag / Last-Modified |

**Verified remote standard.** `https://agentskills.io/.well-known/skills/index.json` → 200,
`{"skills":[{"name":"agent","description":...}]}`. `anthropic.com` and `openai.com` → 404, so
the `.well-known` route is not universal. A discovery RFC exists at
`https://github.com/cloudflare/agent-skills-discovery-rfc` (proposes
`/.well-known/agent-skills/index.json`; still an RFC — **UNVERIFIED** as to ratification).

**Missing layouts not in `SKILL_ROOTS`:** none found that exist on this box, but the shape
`~/.codex/skills`, `./.windsurf/skills`, `./.github/skills` are plausible and cheap to add as
tuple entries.

### 1b. MCP servers — configs, transports, live introspection

Discovery surface is the harness config, always JSON (`mcpServers`) or TOML (`mcp_servers`).
`MCP_CONFIGS` in `router_core.py` already lists 12 files. Verified on disk:
`~/.claude.json` (53 servers, 51 with explicit `type`), `~/.claude/settings.json`,
`~/.gemini/settings.json` (1), `~/.gemini/config/mcp_config.json` (separate file, **not in
`MCP_CONFIGS`** — a real gap), `~/.config/opencode/opencode.json` (9 under `mcp`),
`~/.codex/config.toml`. `~/.cursor/mcp.json`, `~/.vscode/mcp.json`, `./.mcp.json` do not
exist here.

Transport types actually in use: `stdio` ×39, `http` ×19, plus `command` ×11, `local` ×6,
`remote` ×3 (OpenCode/Hermes variants), and 7 `sse` mentions. `transport` is **not recorded in
the index** — only `command/url/type/args` are concatenated into a 200-char `desc`.

**The metadata-rich surface is `tools/list`, not the config.** Measured, with the MCP python
SDK from the Hermes venv:

- `mcp.client.stdio` + `mcp.client.streamable_http.streamable_http_client` (this SDK renamed
  the helper; the old `streamablehttp_client` name raises ImportError).
- 53 declared servers → **36 reachable, 494 tools, 9.7–29.6 s wall** at concurrency 8–10.
  Failure modes are informative and all *should* be index states, not silent drops:
  - `brave-search`: exit 1, `BRAVE_API_KEY environment variable is required` → **config defect**
  - `color-palette`: exit 2, npm bin shim has JS in it (`syntax error near unexpected token`(`) → **broken package**
  - `gmail`: `CERTIFICATE_VERIFY_FAILED … not valid for 'gmail.mcp.googleapis.com'` → **endpoint/transport mismatch**
  - `ExceptionGroup` wrapped errors hide the real exception: unwrap `e.exceptions[0]`.

### 1c. Plugins, slash commands, subagents

- Plugins: `~/.claude/plugins/installed_plugins.json` (version + installPath per plugin, with
  `installedAt`/`lastUpdated`), `~/.claude/plugins/marketplaces/*` (5 git checkouts: SHAs
  `agent-fleet 8f17d5d`, `caveman f5d7294`, `ponytail e3ba2aa`, `claude-plugins-official
  headroom-marketplace f824a27`), `~/.claude/settings.json.enabledPlugins`.
- Slash commands: `~/.claude/commands` is **empty**; all 33 indexed `command` items come from
  plugin `commands/*.md` (3 files across 4 plugin versions — **4 copies of the same command,
  deduped by name to 1**). `~/.codex/prompts` is empty.
- Subagents: `~/.claude/agents` has 3 user agents (`grunt`, `manage`, `reason`); 37 of the 40
  indexed `agent` items come from plugin caches — **9 files across 3 plugin versions of the
  same `caveman` agents**.

### 1d. CLI tools / binaries installed via package managers — entirely absent today

Not one CLI tool is in the index. All of these are machine-readable with no tool execution:

| Surface | Command | Measured | Notes |
|---|---|---|---|
| RPM (system) | `rpm -qa --qf '%{NAME}\t%{SUMMARY}\t%{URL}\t%{LICENSE}\n'` | 3919 pkgs, **1.96 s**, 0.70 MB | summaries = free descriptions |
| RPM binaries | `rpm -qal --qf '%{NAME}\t%{FILENAMES}\n'` → filter `/bin/`,`/sbin/` | 667 803 lines, 3.6 s → **682 bin paths / 646 pkgs** | the real CLI surface; naive `rpm -ql` per pkg took **93 s**, do not |
| npm global | `npm ls -g --depth=0 --json` | 12 pkgs, 3.8 s | only `version`/`overridden`; description needs `registry.npmjs.org` |
| uv tools | `uv tool list` | 16 tools | name+version+entrypoints |
| pipx | `pipx list --short` | 1 | |
| flatpak | `flatpak list --app --columns=application,name` | 6 | |
| PATH dirs | `ls ~/.local/bin` | **115 entries** | user-installed, no metadata source at all |
| cargo | `ls ~/.cargo/bin` | 15 | |

`~/.local/bin` (115) is the sharpest gap: `graphify`, `kwin-mcp`, `shot-scraper`,
`skillspector`, `slop-score`, `comms-hub`, `display-toggle` — real capabilities with **zero**
metadata. `man -w` finds nothing for any of them; only `yt-dlp` ships a man page.

### 1e. systemd user services — absent today, and genuinely routable

145 units via `systemctl --user list-units --all --type=service -o json` (**0.0 s**, includes
`description`). Batched `systemctl --user show <all units> -p Id -p Description -p ExecStart
-p FragmentPath -p UnitFileState` returns **32 blocks in 0.45 s**. 176 service unit files, 22
timers. Real examples with descriptions: `graphify-discover.service` ("graphify auto-discovery
sweep (maps new projects, merges master graph)"), `agent-fleet-www.service`, `storage-guard`.

### 1f. Local scripts/binaries — partially indexed

The fleet store contributes 14 `fleet-tool` + 5 `fleet-plugin` items from
`inventory.json` (regenerated daily by `agent-fleet-catalog.timer`, `generated_at` verified
fresh). But the 82 executable scripts under `~/Work` and the fleet's own
`harness/*.sh`, `lib/common.sh`, `health/*.py` are not first-class; they surface only via
inventory `name`/`kind`.

### 1g. Hosted registries — §2 in full.

---

## 2. Registry / index landscape (all endpoints fetched this session)

Free = usable without payment. Programmatic = documented/supported machine endpoint I hit.

| Registry | Base URL | Free | Programmatic search | Auth / rate limit | Verified response |
|---|---|---|---|---|---|
| **Official MCP Registry** | `https://registry.modelcontextprotocol.io` · `/v0/servers` and `/v0.1/servers` | yes | **yes** | none documented for GET; no rate-limit headers observed; 12/12 burst 200 | 200 JSON `{servers:[{server:{name,description,packages,remotes},_meta:{...official:{status,publishedAt,updatedAt,isLatest}}}]}`; params `cursor,limit,search,updated_since,version,include_deleted`; `search` = name substring only |
| **skills.sh** | `https://skills.sh/api/search?q=` | yes | **yes** | none enforced; 25 rapid calls all 200; `/api/v1/*` needs Vercel OIDC (401) | 200 `{query,searchType:"fuzzy",searchVersion:"algolia",skills:[{id,source,skillId,name,installs}]}`; `limit` works, `n` is ignored (returns 100). Bulk: `sitemap.xml` → 4 children; `sitemap-skills-1.xml` alone = 10 000 URLs |
| **clawhub** | `https://clawhub.ai/api/v1` · `/search?q=` · `/skills?limit=` · `/v1/feeds/skills` | yes | **yes** | **explicit `ratelimit-limit: 3000`, `ratelimit-reset: <s`** headers | 200 with `installs`/`downloads`, `metrics.rolling60DayInstalls`, `topics`, `featured`, canonicalUrl. Feeds: 907 KB skills / 96 KB plugins, `sequence`+`expiresAt` = cheap incremental sync |
| **lobehub** | `https://chat-agents.lobehub.com/index.json` (+ `/{agent_id}.json`) | yes | **yes** (static index, not a query API) | none | 200 `{schemaVersion,agents:[{author,createdAt,identifier,knowledgeCount,meta…}]}` |
| **browse.sh** | `https://browse.sh/api/skills` (+ `/{slug}`) | yes | **yes** | none | 200 `{skills:[{hostname,task,slug,name,title…}]}` |
| **smithery** | `https://registry.smithery.ai/servers?q=&page=1&pageSize=` | yes | **yes** | none observed; `page` is **1-based** (`page=0` → 400 `too_small`) | 200 `{servers:[{id,qualifiedName,namespace,slug,displayName,description…}]}`; `cf-cache-status: HIT, s-maxage=14400` |
| **Hugging Face** | `https://huggingface.co/api/spaces?search=mcp-server&limit=` | yes | **yes** | none | 200 JSON array with `id,likes,trendingScore,sdk,tags` |
| **Glama** | `https://glama.ai/api/mcp/v1/servers` (OpenAPI at `https://glama.ai/api/mcp/openapi.json`) | **no** | spec exists (`query`, `first≤100`, cursor, `sort`) but | **401 "This endpoint requires an API key"**; fields incl `qualityScore`, `tools`, `spdxLicense`, `repository`; license requires visible Glama attribution | 401 verified |
| **PulseMCP** | `https://api.pulsemcp.com/v0.1/servers` | free w/ key | spec'd | **401 "Invalid or missing API key" (`X-API-Key`)**; `v0beta` returns **410 `API_SUNSET`** (random 1% failures from Jan 2026) | 401/410 verified |
| **Composio** | `https://backend.composio.dev/api/v3/tools` | free w/ key | yes | 401 `No authentication provided`; v1 → 410 "upgrade to v3" | 401/410 verified |
| **mcp.so** | `https://mcp.so/servers` (HTML) | free | **no** | `robots.txt`: `Disallow: /api/`, `/search`, `/*?*q=`; JSON Accept → `500 Only HTML requests are supported here` | HTML only; page embeds `total:18232` and category counts in the RSC payload |
| **mcpservers.org** | `https://mcpservers.org` | free | **no** | identical JSON 500 + `/api/` disallowed | HTML only |
| **kiprio** | `https://kiprio.com/v1/mcp-registry/?q=` | free w/ key | yes | **401 "API key required… 500 req/day"** | 401 verified (its marketing page claims keyless; the endpoint disagrees) |
| **GitHub code search** | `https://api.github.com/search/code?q=filename:SKILL.md` | free w/ token | yes | **code search: 10 req/min**; repo search 30/min; core 5000/h | 401 unauthenticated; with `gh` token → 200, `total_count:1255424`. Repo search `topic:mcp-server` → `total_count:33124` |
| **AgentRank** | `https://agentrank-ai.com/api/servers` | claims free | claims REST | `/api/servers` → 404 HTML | **UNVERIFIED** (marketing claims not reproducible) |
| **hermes bulk skills index** | `https://hermes-agent.nousresearch.com/docs/api/skills-index.json` (301 → `nousresearch.github.io`) | yes | bulk snapshot | ETag + `cache-control: max-age=600`; **41.5 MB, `skill_count:101690`** | 200; fields `name,description,source,identifier,trust_level,repo,path,tags,extra`. Cheapest single-shot free skill catalogue, but 41 MB → stream/filter |

**Authoritative specs retrieved:** generic registry API
(`https://github.com/modelcontextprotocol/registry/blob/main/docs/reference/api/generic-registry-api.md`)
— endpoints `/v0.1/servers`, `/{name}/versions`, `/{name}/versions/{version}`, `POST /publish`,
`PATCH .../status`, cursor pagination, "No authentication required by default"; official-registry
additions (`.../official-registry-api.md`) — status values `active|deprecated|deleted`,
`include_deleted`, auth only for publishing (GitHub OAuth/OIDC, DNS, HTTP). `server.json` schema:
`https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`, and the live
registry is currently serving a mix of `2025-09-29` and `2025-12-11` `$schema` values.

**robots.txt verdicts** (fetched): skills.sh `Allow: /` but `Disallow: /api/`, `/search`;
clawhub `Disallow: /api/` yet `Allow: /v1/feeds/*`; browse.sh `Disallow: /api`; smithery.ai
`Disallow: /api/`; mcp.so and glama both `Disallow: /api/`. All of these APIs are
*undocumented-but-live* endpoints — programmatically fine for a private tool, but I would not
build a redistributed service on them, and `robots.txt` is the honest signal that none is a
supported integration surface.

---

## 3. Filesystem watching vs. stat-scan — recommendation

Measured on this box:

| Approach | Cost | Result |
|---|---|---|
| Full `os.walk` over the real roots (incl. plugin cache) | 11 166 dirs / 35 772 files in **0.92 s** cold | always correct, 1.1 s total |
| `listdir`+`stat` over the same set | **0.22 s** (warm cache 0.07 s) | — |
| Discovery roots only (skills trees, no plugin cache) | 1153 SKILL.md paths, **0.022 s cold / 0.033 s warm**; re-stat of cached paths **0.013 s** | — |
| `systemd PathChanged` on `%h/.hermes/skills` | fires in ~1 s | **misses nested category dirs** (verified) |
| `watchdog` 6.0.0 (already in the Hermes venv) | 6 roots armed recursively in **0.20 s**; event latency **26 ms** | sees nested creates |
| raw `ctypes` inotify (`inotify_init1` + `add_watch`) | watch armed, event in **0.1 ms** | works; 16 497 dirs = **2.95 %** of `max_user_watches` (559 138) |
| `watchfiles` 1.1.1 | present in venv, Rust/notify backend | same family as watchdog |
| `inotify_simple` 2.0.1 | pip-available, not installed | needs a dep |
| `fsevents` | macOS only, absent (irrelevant) | — |

**Recommendation: skip the watcher entirely. Use a debounced mtime+size scan, and fix the
broken `PathChanged` unit as the trigger.**

Justification, concretely:

- The corpus is ~1150 indexable skill paths. A full scan is **22–33 ms**; the *entire*
  `build_index` is **79 ms** and the shipped `index_build.py` run is 0.45 s. There is nothing
  to optimise — a watcher would be a permanent daemon, a new failure mode, and a dependency,
  to save 20 ms per reindex.
- **A watcher is not more correct here, it is less.** Proven: an atomic directory rename into a
  watched tree (`git clone` / `tar -x` / fleet sync) produces **zero events** for the moved-in
  subtree with `watchdog`; the events only start once something writes *inside* it. Skills
  arrive exactly that way. A stat-scan cannot miss that.
- inotify watch budgets are fine (2.95 % of the per-user limit) — the constraint is not
  watches, it is that the win is negative.
- `watchdog` is already installed (`watchdog 6.0.0` in `~/.hermes/hermes-agent/venv`), so a
  future out-of-process watcher is one import away. Record that, don't build it.

Concretely: keep `~/.config/systemd/user/tool-router-reindex.path`, but

```ini
[Path]
PathChanged=%h/.hermes/skills
PathChanged=%h/.claude/skills
PathChanged=%h/.claude/plugins/installed_plugins.json
PathChanged=%h/.claude.json
PathChanged=%h/.gemini/config/mcp_config.json
PathChanged=%h/.gemini/settings.json
PathChanged=%h/.config/opencode/opencode.json
PathChanged=%h/.codex/config.toml
PathChanged=%h/.hermes/config.yaml
PathChanged=%h/.local/bin
PathChanged=%h/Work/infra/agent-fleet/catalog/inventory.json
UnitWatchLimitSec=30s          # <-- the fix for unit-start-limit-hit
```

and add a **belt-and-braces timer** (`tool-router-reindex.timer`, every 10 min, `Persistent=true`)
so nothing depends on inotify firing. Add a cheap `SourceModified=` guard inside the reindex so
it self-heals rather than relying on unit state. Note `%h/.hermes/skills` still won't catch
nested creates via `PathChanged`; the timer covers that, and a nested `PathChanged` on the 41
category dirs is not worth the noise.

---

## 4. Freshness, deprecation, and a staleness score

### Signals real ecosystems expose (all verified on this box)

| Signal | Source | Example seen |
|---|---|---|
| Repo archived / `disabled` | `api.github.com/repos/{o}/{r}` | `michaellatman/mcp-get` → `archived: true` |
| Last push | same, `pushed_at` | mcp-get `2026-06-17`; `anthropics/skills` last commit `2026-09-29` |
| Registry lifecycle status | official registry `_meta["…/official"]["status"]` ∈ `active\|deprecated\|deleted` + `statusChangedAt` | 100/100 first-page servers `active` |
| Package deprecated flag | `registry.npmjs.org/{pkg}` top-level `deprecated` | `left-pad`: *"use String.prototype.padStart()"* |
| Release recency | `registry.npmjs.org` `time.modified`; `pypi.org/pypi/{p}` release upload times; `crates.io/api/v1/crates/{c}` `updated_at` | npm `@modelcontextprotocol/server-filesystem` modified `2026-09-17`; crates `ripgrep` `2026-07-15`. Note PyPI `watchdog` 6.0.0 shows upload `2024-11-01` — **mtime of the newest release ≠ recent maintenance**; use it only as a floor |
| Yanked releases | PyPI `releases[*][*].yanked` | watchdog has 2 yanked files |
| Skill-hub state | `~/.hermes/skills/.hub/lock.json` (`source`, `identifier`, `trust_level`, `scan_verdict`, `content_hash`, `install_path`), `.hub/audit.log`, `.hub/quarantine/` | `computer-use-linux` → `skills-sh:community`, `safe`, `sha256:8cd940…`; last audit `INSTALL dbs-chatroom skills.sh:community safe` |
| Upstream update available | `hermes skills check` | 7 s → "3 update(s) available across 15 checked skill(s)" |
| Installed-elsewhere marker | `installed_plugins.json` `lastUpdated`, marketplace git SHA | ponytail `4.9.0` / `4.10.0` both cached |
| Live liveness | `initialize` + `tools/list` | 17/53 failed → config defect / broken package / cert mismatch |
| Local usefulness | `~/.tool-router/gaps.json`, `sourcing.log`, `last_route.json` | 235 gap keys, **222 `served:false`**, 13 served |

`gaps.json` is the best local liveness signal that is not captured anywhere: 222 recorded
requests the corpus failed to serve is a direct measurement of dead weight.

### Proposed score

For each capability compute `staleness = 0.30·A + 0.25·B + 0.20·C + 0.15·D + 0.10·E`, all terms
in `[0,1]`, higher = worse:

- **A — upstream death (0.5 weight, strongest):** `1.0` archived; `0.8` registry status
  `deprecated`/`deleted`; `0.6` > 540 d since last push; `0.4` > 365 d; `0.2` > 180 d; else 0.
  Local-only skills use file age instead: > 540 d → 0.6, > 365 d → 0.3.
- **B — brokenness (0.25):** live probe result — `1.0` missing required env/secret,
  `1.0` spawn crash, `0.8` TLS/endpoint mismatch, `0.5` spawns but `tools/list` times out,
  `0` healthy. Never probed → 0.3 (unknown ≠ fine).
- **C — unused (0.20):** from a usage ledger (§5): `1.0` 0 successful uses and ≥ 180 d indexed;
  `0.6` ≥ 365 d; `0.2` ≥ 90 d; else 0.
- **D — version rot (0.15):** hub-installed with `update_available` → 0.7; installed version
  > 2 releases behind upstream → 0.4; local file untouched while its package ships newer → 0.3.
- **E — content rot (0.10):** `description` empty or unparseable frontmatter → 1.0; description
  < 40 chars or missing `Use when…` trigger → 0.5; 0 otherwise. (This catches the 17 MCP
  servers whose only description is a launch command.)

**Actions**

| Band | `staleness` | Action |
|---|---|---|
| **keep** | `< 0.35` | index normally |
| **flag** | `0.35–0.60` | index with `health:"stale"`; render a staleness badge on the card; add `x-age` tokens so old things lose ties; nudge for hub updates |
| **quarantine** | `> 0.60` **and** (A ≥ 0.6 **or** B ≥ 0.8) | keep the row (so a fix is one reindex away) but drop it from retrieval: exclude from BM25/dense candidates, write a `.tool-router/quarantine.json` entry with the reason, surface the list in `route --doctor`. Never delete user files automatically. |
| **retire** | `> 0.85` for 2 consecutive probes **and** upstream archived/deprecated | propose to the user (`hermes skills uninstall <name>` / remove plugin), with the evidence; still never auto-delete. |

Two consequences worth stating plainly: quarantining must not touch the *dense* vectors
silently (it should set a filter flag, not a re-embed), and `E ≥ 1.0` items (17 MCP servers
with launch-command descriptions) should be auto-flagged the moment the index builds, because
the fix is cheap — `tools/list` already yields better descriptions.

---

## 5. Capability metadata schema

Everything the router already has (`kind,name,desc,extra,path,harness,scope,tokens,name_tokens`)
plus the fields that make routing better. `index.json` is 371 KB for 762 items, so a ~15-field
schema on ~1200 items stays well under 1 MB.

```jsonc
{
  "id": "mcp:context7",                    // stable: kind + name (lower). Identity across reindex.
  "kind": "skill|mcp-tool|mcp|plugin-skill|agent|command|cli|service|script|fleet-tool|fleet-plugin",

  // ---- identity & text
  "name": "context7",
  "desc": "…",                            // tool description for mcp-tool; rpm SUMMARY for cli; unit Description for service
  "summary": "…",                         // ≤200 chars, retrieval-facing
  "tags": ["docs","library","sdk"],       // spec frontmatter tags + derived
  "category": "documentation",            // skill category dir, mcp.so category, rpm group
  "aliases": ["ctx7"],

  // ---- provenance & trust
  "origin": "local|plugin|fleet|remote|package",   // what layer produced it
  "harness": "claude|hermes|opencode|gemini|cursor|agy|system|fleet",
  "scope": "user|project|plugin:<p>",
  "source_path": "/home/…/SKILL.md",       // renamed from `path` (path is also install instructions in inventory.json)
  "source_url": "https://skills.sh/anthropics/skills/pdf",
  "source_id": "skills-sh/anthropic/skills/pdf",
  "content_hash": "sha256:8cd940ec…",     // matches .hub/lock.json content_hash
  "author": "Anthropic",
  "license": "MIT",                        // spec frontmatter license / rpm LICENSE / glama spdxLicense
  "trust_tier": "builtin|official|community|local-unknown|remote-unvetted",
  "scan_verdict": "safe|quarantined|null",  // from .hub/lock.json, not re-derived

  // ---- interface (what it exposes / how to invoke)
  "protocol": "mcp|http|sdk|cli|filesystem|prompt",
  "transport": "stdio|streamable-http|sse|none",
  "entrypoint": "npx -y @modelcontextprotocol/server-brave-search@latest",
  "endpoint": "https://mcp.context7.com/mcp",
  "tool_count": 23,                        // mcp: live count; cli: rpm binary count
  "tools": ["resolve-library-id","query-docs"],   // or child ids, mcp-tool items
  "capabilities": ["tools/list","resources/list"],// MCP protocol capabilities seen at handshake
  "requires": {"env":["BRAVE_API_KEY"],"bins":["node"],"network":true},
  "cost": "free|freemium|paid",            // today: regex guess + fleet note; becomes measured
  "weight_cost_usd": null,                 // null until measured

  // ---- health & freshness (§4)
  "health": "ok|stale|broken|unprobed",
  "health_detail": "exit 1: BRAVE_API_KEY environment variable is required",
  "probed_at": 1791197868,
  "staleness": 0.42,
  "staleness_band": "keep|flag|quarantine|retire",
  "installed_version": "6.0.0",
  "latest_version": "6.0.0",
  "upstream": {"repo":"https://github.com/anthropics/skills","archived":false,"pushed_at":"2026-09-29T02:20:03Z","stars":null,"registry_status":"active"},

  // ---- local usefulness
  "picked_count": 7,                       // times this id surfaced on a card
  "last_picked_at": 1791197365,
  "used_ok_count": 3,                      // session actually loaded/executed it
  "last_used_ok_at": null,
  "fail_count": 2,                         // agent loaded it and it did not help
  "gaps_served": 0,                        // from gaps.json served:true
  "indexed_at": 1791197868,
  "fs_sig": {"mtime_ns":1791197000000000,"size":8072}   // drives the cheap incremental
}
```

Field choices that matter: `mcp-tool` as its own `kind` (484 rows today, invisible to
routing); `tools[]` so a server card can say "23 tools incl. query-docs"; `requires.env` so a
card can warn before the agent trips on a missing key; `health` split from `staleness` because
"broken now" and "old" are different user-facing facts; `fs_sig` because it is the only field
the incremental scan needs to read; `trust_tier` + `scan_verdict` carried straight from
`.hub/lock.json` instead of re-deriving trust.

The missing piece is a **usage ledger**: `route.py` already writes `last_route.json` (picks,
prompt_id, timestamp) and `gaps.json`, but nothing records whether a capability was *used*.
Adding `used_ok` needs only one hook — write `{"id","at","ok"}` lines to
`~/.tool-router/usage.jsonl` when a session loads a skill or an MCP tool returns. Until that
exists, `C` in the staleness formula stays 0 and staleness is upstream-only.

---

## 6. (a) Architecture recommendation + (b) sources to wire

### Architecture: five layers, each independently testable

```
                    ┌─ L1 FILE  (skills/plugins/commands/agents, ~1200 rows, 79 ms)
                    │          parse-only, fs_sig incremental
  capability index ─┤─ L2 CONFIG (MCP declarations, ~60 rows, <1 ms)
                    │          json/toml/yaml, transport + endpoint captured verbatim
                    ├─ L3 LIVE  (MCP tools/list + systemd state, ~600 rows, 10–30 s)
                    │          async, bounded, NEVER on the routing path
                    ├─ L4 OS    (CLIs, services, scripts, ~900 rows, 4–8 s)
                    │          rpm/uv/pipx/npm/flatpak + systemctl + PATH walk
                    └─ L5 REMOTE (registries, lazy, on a gap only)
                               search tier: free+keyless APIs; never bulk into the local index
```

Rules that keep it correct and lazy:

1. **L1–L2 must stay synchronous and cheap** — 79 ms measured. Anything slower is a layer
   behind a stampede guard.
2. **L3/L4 run in the reindex service, never in `route.py`.** Their numbers (10–30 s for MCP
   probes) would wreck route latency. Cache the result to
   `~/.tool-router/index.live.json` with `probed_at`; `route.py` reads only the cache and
   degrades to "unprobed" when it is older than 24 h (`stale_hours` already exists in config).
3. **One writer, atomic swap.** Write `index.json.tmp` → `os.replace`, under the existing
   `building.lock`. Readers never see a half index.
4. **Every layer reports `source_ok`/`source_error` per root.** Today a missing or malformed
   file is silently skipped; that is why `~/.gemini/config/mcp_config.json` was invisible. A
   `sources.json` health block makes coverage auditable: which roots were scanned, how many
   rows each produced, what failed.
5. **Dedupe by `id`, not by name.** `caveman` currently appears 3× (skills + 2 plugin caches),
   `neutrinos-web` 2×, 28 duplicate names overall. Keep every row, mark `duplicate_of`, and let
   the ranker prefer the newest installPath — losing a row hides a real defect, as with
   `dbs-chatroom`, which exists in all four harness trees but is only indexed under
   `harness=agy` because dedupe order reached `~/.gemini/config/skills` first.
6. **Tombstones, not deletions.** Maintain `.tool-router/tombstones.json` of previously indexed
   ids with `removed_at` + reason. Nothing becomes un-indexable silently, and "what did I lose"
   is answerable.
7. **L5 stays a sourcing tier**, exactly as `source.py` does it, but reach **skills.sh
   `/api/search`, clawhub `/api/v1/search`, smithery `registry.smithery.ai/servers`,
   lobehub `index.json`, browse.sh, HF Spaces, and the official registry** directly instead of
   through `hermes skills search` for anything the CLI can't shape. All free and keyless, all
   burst-tested here. Never put remote rows in the local index — they are candidates, not
   capabilities.

### (b) Sources to wire, by priority

**Must (coverage is provably wrong without these)**

| Source | Surface | Cost | Why must |
|---|---|---|---|
| MCP **tools** (`mcp-tool` rows) | `tools/list` over stdio+http | 10–30 s async | 465/484 tools invisible today |
| MCP **config completeness** | add `~/.gemini/config/mcp_config.json`, `~/.config/opencode/config.json`, `~/.hermes/config.yaml mcp_servers` to `MCP_CONFIGS` | ~0 ms | one whole harness's MCPs currently missing |
| Fix `tool-router-reindex.path` | `UnitWatchLimitSec=30s`, more roots | 0 | dead since 2026-10-01; `unit-start-limit-hit` |
| Reindex timer | `OnCalendar=*:0/10`, `Persistent=true` | ~0.45 s/10 min | the only thing that catches nested creates |
| CLI tools from packages | `rpm -qal` + `rpm -qa --qf`, `uv tool list`, `pipx`, `npm ls -g`, `flatpak list` | 4–8 s | 646 rpm CLIs + 16 uv tools, zero indexed |
| systemd user units | `systemctl --user list-units -o json` + batched `show` | 0.5 s | 145 described capabilities |
| `~/.local/bin` walk | listdir + first-line docstring/`--help` sniff | <1 s | 115 user-installed capabilities, no metadata source |
| `usage.jsonl` ledger | one hook in `route.py` | ~0 | required input for staleness term C |

**Should**

| Source | Surface | Cost | Note |
|---|---|---|---|
| Fleet scripts as first-class | extend `discover_fleet_assets` to `harness/`, `lib/`, `health/`, `*.sh` | ~0 ms | only `inventory.json` is read today |
| Plugin provenance | `installed_plugins.json` + marketplace git SHA per item | ~0 | currently the *cache path* is the only identity |
| Duplicates | `duplicate_of` + rank preference | ~0 | 28 duplicate names |
| Staleness scoring | §4 | ~0 | needs usage ledger |
| `hermes skills check` ingest | parse its 3-updates/15-skills output nightly | 7 s | free upstream-freshness |
| skills.sh `/api/search` direct | keyless, keyless rate limit | ~0.4 s/query | richer than the CLI wrapper |
| Remote `.well-known/skills/index.json` | HTTP GET | ~0.3 s | spec exists; only 1 host verified live |

**Could**

| Source | Surface | Cost | Note |
|---|---|---|---|
| clawhub feeds | `/v1/feeds/skills`, `/v1/feeds/plugins` | 1 MB | `sequence`/`expiresAt` = clean incremental |
| smithery direct | `registry.smithery.ai/servers?page=1` | ~0.8 s | keyless; 1-based paging |
| HF Spaces | `huggingface.co/api/spaces?search=mcp-server` | ~0.4 s | `likes`/`trendingScore` as quality proxy |
| Hermes bulk skills index | 41.5 MB `skills-index.json` | stream + filter | `skill_count:101690`, ETag-cached — the single best free catalogue, but stream it |
| github code/repo search | authenticated `gh` | quota-bound | 10/min code, 30/min repo; needs a token |
| Glama / PulseMCP / Composio / kiprio | — | key required | **all 401 without keys**; wire only if a key is provisioned |
| mcp.so / mcpservers.org | — | — | no free programmatic API; `robots.txt` disallows `/api/`; skip |
| man pages / shell completions | `man -w`, bash/zsh completion files | ~1 s | only 1 of 5 probed binaries ships a man page |

**Explicitly not recommended:** bulk-inlining any remote registry into the local index.
101 690 skills / 18 232 mcp.so servers locally indexed would destroy the current precision
(`min_score 0.28`, `max_skills 4`) and cost more than the whole index. Remote = gap-time
sourcing only.

---

## 7. State of the live box (research side effects, disclosed)

- `systemctl --user reset-failed tool-router-reindex.path` + `start` — the watcher was dead
  (`unit-start-limit-hit` since 2026-10-01 17:17:41) and is now `active (success)`. **I did not
  edit the unit files**; the `UnitWatchLimitSec=30s` fix in §3 is still unwritten.
- Rebuilt the index twice while testing: `~/.tool-router/index.json` is now **762 items**
  (was 759; the probe skill was added and removed cleanly, ending where disk says it should be).
- Created and deleted four probe paths (`__probe_skill__`, `__probe_watch__`,
  `__probe_watch2__`, `__wd_probe__`); all removed, verified.
- Wrote throwaway artifacts only: `/tmp/mcp_probe.json`, `/tmp/mcp_tools.json`, `/tmp/rpmbins2.tsv`.
- This report: `~/Work/tool-router/research/CAPABILITY-DISCOVERY-2026-10-05.md`.

**Unverified claims** (could not confirm from a retrieved source): the Cloudflare
`.well-known/agent-skills` RFC's status; AgentRank's advertised REST API; any PulseMCP rate-limit
ceiling (key-gated); exact Smithery quotas beyond `s-maxage:14400`; whether `~/.codex/prompts`,
`~/.cursor/mcp.json`, `~/.vscode/mcp.json` exist on other machines (absent here).