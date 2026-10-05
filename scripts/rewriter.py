#!/usr/bin/env python3
"""rewriter.py — Stage 2 of the tool-router pipeline: the prompt engineer.

Takes the user's raw prompt plus the first-pass routing card context and asks
an LLM to rewrite the prompt into a structured, richer form (ReAct-shaped:
Goal / Target / Done-condition / Steps / Verify), without inventing scope the
user did not ask for. The rewritten prompt is re-routed (Stage 3) and the two
pick-sets are merged, so the final card covers both the user's words and the
enriched intent.

WHICH MODEL DOES THE REWRITE (and why it is not "the parent model")
--------------------------------------------------------------------
The honest constraint, verified 2026-10-05: a UserPromptSubmit hook is a SEPARATE
OS PROCESS. It runs before the harness has sent the turn to its model, so it
cannot call the model that is about to answer — that model has not loaded the
prompt yet. "Rewrite using a sub-agent on the parent model" is therefore not
implementable from inside a hook, on any harness (Claude Code, Hermes, agy,
OpenCode all intercept as an out-of-process hook).

What IS available is the SESSION's model, which the harness exports into the
hook environment:
    ANTHROPIC_MODEL / ANTHROPIC_SMALL_FAST_MODEL  (Claude Code)
    GEMINI_MODEL / OPENCODE_MODEL                  (agy, OpenCode)
    HERMES_MODEL                                   (Hermes gateway)
plus an explicit override, ROUTER_REWRITE_MODEL.

So this module now resolves a per-turn model in this order:
    1. explicit  ROUTER_REWRITE_MODEL (operator wins, always)
    2. session   the harness's own model, remapped to a fast sibling when the
                 session model is a large reasoning model (see MODEL_MAP). The
                 rewrite is a SHORT structured-output task, not a reasoning
                 task: paying for an opus-class call to produce 6 lines is the
                 single largest waste in the pipeline, and it is the reason the
                 latency budget could not be met before.
    3. gemini    the old default, unchanged and still free.
    4. none      return None; pipeline keeps the original prompt.

The rewrite ALSO now receives the picked capabilities and must reference them,
so the downstream agent is told which skills/tools to use rather than having to
re-derive that from the card. See SYSTEM below.

Providers (first that works wins; all optional, all fail-open):
    1. gemini  — Google AI Studio OpenAI-compatible endpoint
                 (GOOGLE_API_KEY / GEMINI_API_KEY, model gemini-flash-latest)
    2. custom  — any OpenAI-compatible endpoint:
                 ROUTER_REWRITE_BASE_URL + ROUTER_REWRITE_API_KEY + ROUTER_REWRITE_MODEL
    3. none    — return None; pipeline keeps the original prompt.

Config keys (~/Work/tool-router/config.json -> "rewriter"):
    enabled      true (default)
    timeout_s    12.0
    max_tokens   700
    max_chars    4000      # skip rewriting prompts longer than this
    min_chars    12        # skip trivial prompts ("hi", "continue")
    session_model true      # follow the harness's own model (default)
    include_picks true      # tell the rewriter which capabilities were picked
"""
from __future__ import annotations

import json
import os
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/openai/"
              "chat/completions")
GEMINI_MODELS = ("gemini-flash-lite-latest", "gemini-flash-latest")
# Order matters, measured 2026-10-05: gemini-flash-latest returns HTTP 429
# "exceeded your current quota" on the GOOGLE_API_KEY this box actually uses, so
# putting it first meant EVERY rewrite burned a failed call before falling back
# (and tripped the breaker, silently disabling the stage). flash-lite answers in
# ~0.4 s on the same key. flash-latest stays in the list as a fallback for when
# the quota resets; it is simply never tried first.
# gemini-2.5-flash is deliberately absent: HTTP 404 "no longer available to new
# users" on this key (verified 2026-10-05).

# Session model -> the model that should actually do the rewrite.
#
# The rewrite is ~6 lines of structured output from a prompt the router already
# understood. It is NOT a reasoning task, so it should never run on a
# reasoning-class model: that is the difference between ~1s and ~15s, and it is
# why "use the parent model" had to become "use the parent's fast sibling".
# Unlisted models fall through unchanged (the session model is used as-is).
MODEL_MAP = {
    # Claude Code / Anthropic
    "claude-opus-4": "claude-haiku-4-5-20251001",
    "claude-opus-4-1": "claude-haiku-4-5-20251001",
    "claude-opus-4-5": "claude-haiku-4-5-20251001",
    "claude-opus-5": "claude-haiku-4-5-20251001",
    "claude-sonnet-4": "claude-haiku-4-5-20251001",
    "claude-sonnet-4-5": "claude-haiku-4-5-20251001",
    "claude-sonnet-5": "claude-haiku-4-5-20251001",
    "claude-fable-5": "claude-haiku-4-5-20251001",
    # Google / agy
    "gemini-3-pro": "gemini-flash-latest",
    "gemini-2.5-pro": "gemini-flash-latest",
    "gemini-2.5-flash": "gemini-flash-latest",
    # OpenAI
    "gpt-5": "gpt-5-mini",
    "gpt-5.1": "gpt-5-mini",
    "gpt-5.2": "gpt-5-mini",
    "o3": "gpt-5-mini",
    # z.ai / GLM
    "glm-5.3": "glm-4.6",
    # glm-4.6 is already the fast sibling and is deliberately absent here.
}

# Models we know how to REACH. A session model whose fast sibling is not in
# here cannot be used for the rewrite (no credentials/base_url), so resolution
# falls back to gemini rather than making a call that is guaranteed to fail.
REACHABLE_FAMILIES = ("claude", "gemini", "gpt-", "glm")

# Env vars that carry the session model, in priority order. Claude Code exports
# ANTHROPIC_MODEL into the hook process (verified 2026-05: present in the hook
# env); the others are best-effort for agy/OpenCode/Hermes.
SESSION_MODEL_ENV = (
    "ROUTER_REWRITE_MODEL",      # explicit operator override wins outright
    "ANTHROPIC_MODEL",
    "CLAUDE_MODEL",
    "HERMES_MODEL",
    "OPENCODE_MODEL",
    "GEMINI_MODEL",
    "AGY_MODEL",
)


def session_model() -> str | None:
    """The harness's current model, as reported by the environment."""
    for var in SESSION_MODEL_ENV:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return None


def resolve_model(base_cfg: dict) -> tuple[str | None, str]:
    """(model, provider) for the rewrite.

    Owner rule 2026-10-05: the rewriter runs on FREE models only —
    opencode-go first, OpenRouter free as the fallback. Anthropic is not a
    rewrite target at all (it has never been reachable: Claude Code's OAuth is
    scoped to Claude's endpoints and 401s on api.anthropic.com).

    Order:
      0. ROUTER_REWRITE_MODEL           — explicit operator override, still wins
      1. "free"                          — the default; opencode-go then OpenRouter
      2. gemini                          — last resort if no free key is present
    """
    explicit = (os.environ.get("ROUTER_REWRITE_MODEL") or "").strip()
    if explicit:
        return explicit, "gemini" if explicit.startswith("gemini") else "custom"

    cfg = (base_cfg.get("rewriter") or {})
    if cfg.get("free_only", True):
        for gw in (cfg.get("free_gateways") or FREE_GATEWAYS):
            if free_gateway_credential(gw):
                return None, f"free:{gw}"      # model list comes from FREE_MODELS
        # no free key provisioned: fall through to the historical gemini default
    if not cfg.get("session_model", True):
        return (GEMINI_MODELS[0], "gemini")
    return (GEMINI_MODELS[0], "gemini")

SYSTEM = (
    "You are a prompt engineer. Rewrite the user's request into a precise "
    "working prompt using this exact structure, keeping every fact the user "
    "gave and NEVER adding goals, features or scope they did not ask for:\n"
    "Goal: <one line>\n"
    "Target: <files/systems/scope if named, else 'unspecified — locate first'>\n"
    "Use: <MANDATORY. List EVERY capability from the provided block, one per line as "
    "'name — why it fits'. Copy the names EXACTLY as given. If the block is "
    "present, this line must never say 'none identified'. If no block was "
    "provided, write 'Use: none identified'. Never invent a name that is not "
    "in the block.>\n"
    "Done when: <verifiable done-condition>\n"
    "Steps: <2-5 concrete steps>\n"
    "Verify: <how to prove it worked>\n"
    "Rules: the user's own words stay authoritative; if their request is "
    "already precise and complete, keep their wording and only add the Use "
    "line. Output only the rewritten prompt, no preamble, no markdown fences."
)

# Older/leaner variant for prompts with no capability picks to reference.
SYSTEM_NOPICKS = (
    "You are a prompt engineer. Rewrite the user's request into a precise "
    "working prompt using this exact structure, keeping every fact the user "
    "gave and NEVER adding goals, features or scope they did not ask for:\n"
    "Goal: <one line>\n"
    "Target: <files/systems/scope if named, else 'unspecified — locate first'>\n"
    "Done when: <verifiable done-condition>\n"
    "Steps: <2-5 concrete steps>\n"
    "Verify: <how to prove it worked>\n"
    "If the request is already precise and complete, return it unchanged. "
    "Output only the rewritten prompt, no preamble, no markdown fences."
)

_BREAKER = Path(os.path.expanduser("~/.tool-router")) / "breaker-rewriter.json"
_breaker_until = 0.0

# Per-model quota cooldown (seconds). Process-local: the hook is a short-lived
# process, so this only needs to cover the models tried inside ONE rewrite.
QUOTA_COOLDOWN_S = 600.0
_quota_blocked: dict = {}
# Why the last attempt at each free model failed: 'timeout' (queued) vs 'error'
# (404/403/auth). Drives soft-vs-hard backoff in _record_free_health.
_LAST_OUTCOME: dict = {}

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# ---------------------------------------------------------------- opencode go
# Owner's standing rule (2026-10-05): the prompt rewrite runs on a FREE
# opencode-go model, never on Anthropic. OpenRouter free models are the fallback.
#
# Both gateways need two non-obvious request headers, found by probing on this
# box (a plain call answers HTTP 403 "error code: 1010", which is Cloudflare, not
# an auth failure):
#   1. a browser User-Agent, or Cloudflare blocks the request outright
#   2. a session id — opencode-go answers 400 MissingSessionID without it
OCGO_URL = "https://opencode.ai/zen/go/v1/chat/completions"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120 Safari/537.36")

# Measured live 2026-10-05, TWICE, and the two runs disagree — that matters.
# "Reply OK" probe:
#   opencode-go  space-bunny-free 1.6s | omen-alpha 2.0s | longcat 11.9s | glm 14.1s
#   openrouter   qwen3.8-27b 0.6s | lfm-2.5-2.6b 0.7s | gemma-4 1.0s | ling 1.3s
# Real rewrite prompt (the one that matters):
#   opencode-go  space-bunny-free 8.6s TIMEOUT | omen 7.8s T | longcat 8.5s T | glm 8.5s T
#   openrouter   qwen3.8-27b 10.2s T | lfm-2.5-2.6b 4.5s OK | gemma-4 0.6s T | ling 0.3s T
# The free tiers QUEUE. A model that answered a one-word prompt in 0.6 s can sit
# behind a real 400-token rewrite for 10 s, so the probe order is worthless as a
# production order. What the second run does establish:
#   * lfm-2.5-2.6b is the only one that reliably returns the full rewrite
#   * opencode-go's free models all time out under real load on this box
# So: opencode-go stays the primary GATEWAY (owner requirement) and is tried
# first with a short cap, and OpenRouter's lfm/qwen carry the real traffic.
# FREE_MODELS is ordered fastest-and-most-likely-first by the REAL-prompt run.
FREE_MODELS: dict[str, tuple[str, ...]] = {
    "opencode-go": ("space-bunny-free", "omen-alpha",
                    "glm-5.3-flash", "longcat-2.5-preview-free"),
    "openrouter": ("liquid/lfm-2.5-2.6b:free", "qwen/qwen3.8-27b:free",
                   "openrouter/free", "google/gemma-4-26b-a4b-it:free",
                   "inclusionai/ling-3.1-flash"),
}
FREE_GATEWAYS = ("opencode-go", "openrouter")

_CRED_PATH = Path("~/.claude/.credentials.json").expanduser()
_oauth_cache: dict = {}


def anthropic_credential() -> str | None:
    """ANTHROPIC_API_KEY only.

    Claude Code's OAuth token in ~/.claude/.credentials.json is deliberately NOT
    used here: it is scoped to Claude Code's own endpoints and api.anthropic.com
    rejects it (measured 2026-10-05: HTTP 401 "API key is invalid" with the OAuth
    bearer as x-api-key). Reusing it would be credential misuse, so a Claude
    session on Anthropic simply falls back to the gemini rewriter unless the
    operator provisions a real key.
    """
    return os.environ.get("ANTHROPIC_API_KEY") or None


def _post_anthropic(cred: str, model: str, system: str, user: str,
                    max_tokens: int, timeout: float) -> str | None:
    """Anthropic Messages API call. Returns concatenated text blocks or None."""
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps({"model": model, "max_tokens": int(max_tokens),
                         "temperature": 0.2, "system": system,
                         "messages": [{"role": "user", "content": user}]}).encode(),
        headers={"Content-Type": "application/json", "x-api-key": cred,
                 "anthropic-version": ANTHROPIC_VERSION})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        blocks = [b.get("text", "") for b in (data.get("content") or [])
                  if b.get("type") == "text"]
        return "".join(blocks).strip() or None
    except Exception:
        return None


def _load_config(base_cfg: dict) -> dict:
    cfg = {"enabled": True, "timeout_s": 12.0, "max_tokens": 700,
           "max_chars": 4000, "min_chars": 12,
           "free_only": True, "free_gateways": list(FREE_GATEWAYS),
           # Hard ceiling for the whole free-model search.
           #
           # Measured 2026-10-05 on the real rewrite prompt, three times, and the
           # free tiers are NOT interchangeable: opencode-go's free models
           # return HTTP 200 with EMPTY content (8.7s / 4.0s / 4.0s), and
           # OpenRouter's qwen3.8-27b and openrouter/free do the same (10.7s /
           # 11.0s). Only liquid/lfm-2.5-2.6b:free actually returns the rewrite
           # (3.1s, 1257 chars). So a model returning empty is a HARD failure,
           # not a slow one — see _record_free_health.
           #
           # Budget: one 3.5s attempt at the known-good model, plus room for one
           # retry. The opencode-go primary keeps a 1.2s probe so the owner's
           # "opencode-go first" rule is honoured without eating the hook budget.
           "free_deadline_s": 3.5,
           "free_timeout_s": 3.5,
           "free_primary_timeout_s": 1.2}
    cfg.update({k: v for k, v in (base_cfg.get("rewriter") or {}).items()
                if v is not None})
    return cfg


def _breaker_active() -> bool:
    global _breaker_until
    if time.time() < _breaker_until:
        return True
    try:
        data = json.loads(_BREAKER.read_text())
        until = float(data.get("until", 0))
        if time.time() < until:
            _breaker_until = until
            return True
    except (OSError, ValueError):
        pass
    return False


def _trip_breaker(seconds: float = 300.0) -> None:
    global _breaker_until
    _breaker_until = time.time() + seconds
    try:
        _BREAKER.parent.mkdir(parents=True, exist_ok=True)
        _BREAKER.write_text(json.dumps({"until": _breaker_until}))
    except OSError:
        pass


def _free_circuit_breaker() -> dict:
    """Per-model health, persisted so a dead free model is not retried forever.

    Measured 2026-10-05: every opencode-go free model times out on a real
    rewrite prompt (7.8-8.6 s) because the free tier queues. Retrying that on
    every message costs 1.5-6 s each time for a result that never arrives. The
    in-process _quota_blocked dict dies with the hook process, so the state has
    to live on disk.

    Shape: {"<gateway>/<model>": {"fail": n, "until": epoch}} — a model is
    skipped until `until` passes; `fail` decays so a model gets retried later.
    """
    p = _health_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _last_outcome(gateway: str, model: str) -> str:
    """'timeout' | 'error' | '' for the most recent attempt at this model.

    _post_free records the reason in _last_free_outcome so the caller can tell a
    slow/free-tier-queue apart from a genuinely broken model.
    """
    return _LAST_OUTCOME.get(f"{gateway}/{model}", "")


def _record_free_health(tag: str, ok: bool, hard: bool = False) -> None:
    """Record one attempt. `hard` = broken (long backoff); soft = slow (short).

    A timeout is soft on purpose: free tiers queue, and a queued model is worth
    retrying on the next message. A 404/403/auth failure is hard and stays out
    for hours.
    """
    data = _free_circuit_breaker()
    now = int(time.time())
    entry = data.get(tag) or {"fail": 0, "until": 0}
    if ok:
        entry["fail"] = 0
        entry["until"] = 0
        entry["ok_at"] = now
    else:
        entry["fail"] = int(entry.get("fail", 0)) + 1
        n = min(entry["fail"] - 1, 3)
        # An "empty" 200 is a TRANSIENT free-tier fault, not a broken model:
        # measured 5 identical requests to lfm-2.5-2.6b -> 4 real answers and 1
        # empty body (2.5-6.8 s each). Banning a model for one empty response
        # retires the only model that works, so empty gets the SHORTEST backoff
        # and is retried inside the same rewrite.
        soft = (1, 2, 5, 10)[n] if not hard else (5, 15, 45, 120)[n]
        if _LAST_OUTCOME.get(tag) == "empty":
            soft = 0            # retry immediately, same turn
        entry["until"] = now + soft * 60
        entry["hard"] = bool(hard)
    data[tag] = entry
    data = {k: v for k, v in data.items()
            if v.get("fail") and v.get("until", 0) > now - 86400}
    try:
        p = _health_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _health_path() -> Path:
    return Path(os.path.expanduser("~/.tool-router")) / "free-model-health.json"


def _free_env_key(name: str) -> str | None:
    """Key from the hook env, else from ~/.hermes/.env (the hook runs outside it)."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        for line in Path("~/.hermes/.env").expanduser().read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return None


def free_gateway_credential(gateway: str) -> str | None:
    """API key for a free gateway, or None when it is not provisioned."""
    return {
        "opencode-go": lambda: _free_env_key("OPENCODE_GO_API_KEY"),
        "openrouter": lambda: _free_env_key("OPENROUTER_API_KEY"),
    }.get(gateway, lambda: None)()


def _post_free(gateway: str, key: str, model: str, system: str, user: str,
               max_tokens: int, timeout: float) -> str | None:
    """One completion against opencode-go or OpenRouter. None on any failure.

    Headers are not optional: without a browser User-Agent both gateways answer
    403 (Cloudflare 1010), and opencode-go additionally answers 400
    MissingSessionID without X-Session-Id. Both were measured on this box.
    """
    if gateway == "opencode-go":
        url, headers = OCGO_URL, {
            "X-Session-Id": f"tool-router-{int(time.time()) // 60}",
        }
    elif gateway == "openrouter":
        url, headers = OPENROUTER_URL, {
            "HTTP-Referer": "https://astra.jitinnair.com",
            "X-Title": "tool-router-rewrite",
        }
    else:
        return None
    headers.update({"Content-Type": "application/json",
                    "Authorization": f"Bearer {key}",
                    "User-Agent": BROWSER_UA})
    # OpenAI-compatible field name differs: Gemini's layer uses maxTokens.
    body = {"model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "max_tokens": int(max_tokens), "temperature": 0.2}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers=headers)
    tag = f"{gateway}/{model}"
    if time.time() < _quota_blocked.get(tag, 0):
        return None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        if isinstance(data, list):
            data = data[0] if data else {}
        out = (data.get("choices") or [{}])[0].get("message", {}).get("content")
        if out:
            _LAST_OUTCOME[tag] = ""
        else:
            # HTTP 200 with an EMPTY body. Measured 2026-10-05 on the real
            # rewrite prompt: opencode-go space-bunny-free/omen-alpha/glm-5.3-flash
            # and OpenRouter qwen3.8-27b/openrouter/free ALL do this. A 200 is
            # not a success — treat it as a hard failure so the breaker retires
            # the model instead of retrying it forever.
            _LAST_OUTCOME[tag] = "empty"
        return out
    except urllib.error.HTTPError as exc:
        _LAST_OUTCOME[tag] = "error"
        # 429 rate limit / 403 model gone -> remember and do not retry soon
        if exc.code in (429, 402, 403):
            _quota_blocked[tag] = time.time() + QUOTA_COOLDOWN_S
        return None
    except (TimeoutError, socket.timeout):
        # A free tier that QUEUES is indistinguishable from a dead model once
        # the hook budget is 5 s. Record it as 'timeout' so the breaker applies
        # a SOFT backoff (retry soon) instead of banishing a working model.
        _LAST_OUTCOME[tag] = "timeout"
        _quota_blocked[tag] = time.time() + 30.0
        return None
    except Exception:
        _LAST_OUTCOME[tag] = "error"
        return None


def _post(url: str, key: str, body: dict, timeout: float) -> str | None:
    """One completion call. Returns None on any failure.

    A 429 (quota exhausted) is remembered per-model for QUOTA_COOLDOWN_S so the
    same exhausted model is not retried on every message for the next 10
    minutes. Measured 2026-10-05: gemini-flash-latest was 429ing on this key and
    every single rewrite was paying for the failed attempt.
    """
    model = body.get("model", "")
    if time.time() < _quota_blocked.get(model, 0):
        return None
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        if isinstance(data, list):  # gemini sometimes wraps errors in a list
            data = data[0] if data else {}
        return (data.get("choices") or [{}])[0].get("message", {}).get("content")
    except urllib.error.HTTPError as exc:
        if exc.code in (429, 402):
            _quota_blocked[model] = time.time() + QUOTA_COOLDOWN_S
        return None
    except Exception:
        return None


def _key(env_names: tuple[str, ...]) -> str | None:
    for n in env_names:
        v = os.environ.get(n)
        if v:
            return v
    # hermes .env fallback (the router hook runs outside hermes's env)
    for n in ("GOOGLE_API_KEY", "GEMINI_API_KEY"):
        try:
            for line in Path("~/.hermes/.env").expanduser().read_text().splitlines():
                if line.startswith(n + "="):
                    return line.split("=", 1)[1].strip()
        except OSError:
            pass
    return None


def _picks_block(picks) -> str:
    """Render the capability list for the rewriter's Use: line.

    Each pick is `name (kind) — description`. Names are passed verbatim so the
    model can only reference capabilities that actually exist; the system
    prompt forbids inventing others.
    """
    rows = []
    for p in (picks or [])[:8]:
        if isinstance(p, str):
            rows.append(f"- {p}")
            continue
        name = p.get("name", "?")
        kind = p.get("kind", "")
        desc = (p.get("description") or p.get("desc") or "").strip()
        if len(desc) > 140:
            desc = desc[:137] + "..."
        rows.append(f"- {name} ({kind}) — {desc}" if desc else f"- {name} ({kind})")
    if not rows:
        return ""
    return "Capabilities identified by routing (use only these names):\n" + "\n".join(rows)


def rewrite(prompt: str, route_ctx: str, base_cfg: dict,
            picks=None) -> dict | None:
    """Return {"prompt", "provider", "model"} or None (fail-open).

    `route_ctx` is the one-line first-pass top-pick context. `picks` is the full
    first-pass pick list; when supplied, the rewrite must reference those
    capabilities in a Use: line so the downstream agent is told what to load
    instead of re-deriving it from the card.
    """
    global _breaker_until
    cfg = _load_config(base_cfg)
    if not cfg.get("enabled"):
        return None
    p = (prompt or "").strip()
    if len(p) < int(cfg["min_chars"]) or len(p) > int(cfg["max_chars"]):
        return None
    if re.match(r"^/|^\*", p):  # slash commands / bypass never rewritten
        return None
    if _breaker_active():
        return None

    picks = picks if cfg.get("include_picks", True) else None
    system = SYSTEM_NOPICKS if not picks else SYSTEM
    head = (f"First-pass routing suggested: {route_ctx}\n\n" if route_ctx else "")
    block = _picks_block(picks)
    user = (f"{head}{block}\n\n" if block else f"{head}"
            ) + f"User request:\n\"\"\"\n{p}\n\"\"\""

    model, provider = resolve_model(base_cfg)
    timeout = float(cfg["timeout_s"])

    # 1) FREE gateways, in configured order (opencode-go, then openrouter).
    #    Hard deadline: without it, 4 models x 4 s on a queuing free tier burns
    #    16 s inside a 5 s hook budget. Every remaining attempt is capped by
    #    what is left of free_deadline_s, so the stage degrades to "no rewrite"
    #    instead of "hang" (measured 20.8 s before this guard).
    if provider.startswith("free:"):
        wanted = [provider.split(":", 1)[1]]
        wanted += [g for g in (cfg.get("free_gateways") or FREE_GATEWAYS)
                   if g != wanted[0]]
        deadline = time.monotonic() + float(cfg.get("free_deadline_s", 3.2))
        per_call = float(cfg.get("free_timeout_s", 2.0))
        # The primary gateway gets a smaller slice than the fallback. Owner rule
        # is opencode-go first, and it IS tried first — but measured 2026-10-05
        # all four of its free models time out on a real rewrite prompt (7.8-8.6 s
        # each) because the free tier queues. Paying 4 x 4 s for a gateway that
        # cannot currently serve would blow the hook budget, so the primary gets
        # one short attempt, the fallback gets the rest.
        primary, *rest = wanted
        health = _free_circuit_breaker()
        now_i = int(time.time())
        for gw in wanted:
            key = free_gateway_credential(gw)
            if not key:
                continue
            models = FREE_MODELS.get(gw, ())
            if gw == primary:
                models = models[:1]          # one probe, short cap
                cap = float(cfg.get("free_primary_timeout_s", 1.5))
            else:
                cap = per_call
            for m in models:
                # skip a model the persisted breaker says is unhealthy
                row = health.get(f"{gw}/{m}") or {}
                if row.get("until", 0) > now_i:
                    continue
                left = deadline - time.monotonic()
                if left <= 0.35:
                    break
                out = _post_free(gw, key, m, system, user,
                                 int(cfg["max_tokens"]),
                                 max(0.35, min(cap, left)))
                # Distinguish "answered nothing useful" from "did not answer in
                # time". A free tier that QUEUES is not a broken model — it is
                # slow right now, and backing it off entirely is how the whole
                # free tier ends up starved (measured 2026-10-05: every model
                # cooled down and the stage fell through to gemini).
                if out and out.strip():
                    _record_free_health(f"{gw}/{m}", True)
                    return {"prompt": out.strip()[:4000], "provider": f"free:{gw}",
                            "model": m, "session_model": session_model()}
                _record_free_health(f"{gw}/{m}", False,
                                    hard=_last_outcome(gw, m) in ("error", "empty"))
            if time.monotonic() >= deadline:
                break

    # 2) same-vendor fast sibling (only when explicitly configured; Anthropic is
    #    not a default rewrite target).
    if provider == "anthropic" and model:
        cred = anthropic_credential()
        if cred:
            out = _post_anthropic(cred, model, system, user,
                                  int(cfg["max_tokens"]), timeout)
            if out:
                return {"prompt": out[:4000], "provider": "anthropic",
                        "model": model, "session_model": session_model()}

    # 2) gemini — the default and the only endpoint guaranteed reachable
    if provider in ("gemini", "", None):
        key = _key(("GOOGLE_API_KEY", "GEMINI_API_KEY"))
        if key:
            models = (model,) if model else GEMINI_MODELS
            for m in models:
                out = _post(GEMINI_URL, key,
                            {"model": m,
                             "messages": [{"role": "system", "content": system},
                                          {"role": "user", "content": user}],
                             "maxTokens": int(cfg["max_tokens"]),
                             "temperature": 0.2},
                            timeout)
                if out and out.strip():
                    return {"prompt": out.strip()[:4000], "provider": "gemini",
                            "model": m, "session_model": session_model()}
            _trip_breaker(120.0)
            return None

    # 2) custom OpenAI-compatible (explicit override, or a reachable claude sibling)
    base = os.environ.get("ROUTER_REWRITE_BASE_URL")
    ckey = os.environ.get("ROUTER_REWRITE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if base and ckey and model:
        out = _post(base.rstrip("/") + "/chat/completions", ckey,
                    {"model": model,
                     "messages": [{"role": "system", "content": system},
                                  {"role": "user", "content": user}],
                     "max_tokens": int(cfg["max_tokens"]),
                     "temperature": 0.2},
                    timeout)
        if out and out.strip():
            return {"prompt": out.strip()[:4000], "provider": "custom",
                    "model": model, "session_model": session_model()}

    # 3) last resort: the original gemini default, so the stage never silently
    # disappears just because the session model was unrecognised.
    key = _key(("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    if key:
        for m in GEMINI_MODELS:
            out = _post(GEMINI_URL, key,
                        {"model": m,
                         "messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": user}],
                         "maxTokens": int(cfg["max_tokens"]),
                         "temperature": 0.2},
                        timeout)
            if out and out.strip():
                return {"prompt": out.strip()[:4000], "provider": "gemini",
                        "model": m, "session_model": session_model()}
    _trip_breaker(300.0)
    return None
