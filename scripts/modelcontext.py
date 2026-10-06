#!/usr/bin/env python3
"""modelcontext.py — ToolR's per-turn model/limits/cost resolver.

Feeds the "decision engine": how many picks the card should carry given the
session model's context window, cost tier, and quota posture.

Sources, in priority order (all optional, all fail-open):
  1. hook env vars    ANTHROPIC_MODEL / CLAUDE_MODEL / HERMES_MODEL /
                      OPENCODE_MODEL / GEMINI_MODEL / AGY_MODEL
                      (verified: Claude Code exports ANTHROPIC_MODEL into hook
                      env; the rest are best-effort per rewriter.py's mappings)
  2. hook payload     Claude Code's UserPromptSubmit JSON sometimes carries
                      model hints; checked but not trusted
  3. model-overrides  ~/.tool-router/model-overrides.json, user-editable:
                        {"<model-or-family-substr>": {"ctx": 200000,
                          "in_cost": 3.0, "out_cost": 15.0, "tier": "premium"}}
                      wins over the built-in profile for anything it names
  4. builtin profile  MODEL_PROFILES below, substring-matched most-specific-first
  5. default          the DEFAULT_PROFILE (unknown model → conservative)

The resolver NEVER calls the network on the hot path. Pricing is data updated
by `refresh()` (writes the model-overrides.json), which fetches OpenRouter's
public models endpoint (rate-limit status is out of scope: ToolR only ever
needs the model's catalog facts, not the user's remaining balance — most
harnesses do not expose that in hook env, and polling a paid billing API per
prompt is exactly the network hop this module is forbidden to make. It is a
one-liner to add later if a harness ever exports quota.)

Decision policy (decision(cfg, profile) -> card count + hints):
  - Ask-that-count wins everywhere (user said "top 20"; enforced upstream).
  - Otherwise: small-context models get fewer picks (the card itself is
    ~900 chars; 10 picks ≈ 1.8k chars — noise on a 32k ctx model, nothing on
    a 400k one). Cost tiers shape a soft ceiling: premium models on small
    windows get trimmed hardest;
  - and the whole thing is overridable: config "decision" block, or
    TOOLR_MAX_PICKS env.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

BASE_DIR = Path(os.path.expanduser(
    os.environ.get("TOOL_ROUTER_HOME", "~/.tool-router")))
OVERRIDES_PATH = BASE_DIR / "model-overrides.json"
CACHE_PATH = BASE_DIR / "model-catalog-cache.json"
CACHE_TTL_S = 7 * 86400
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_TIMEOUT_S = 8.0

# ---- built-in profiles (substring match, most-specific first) ------------
# ctx  = context window tokens; tier: fast < balanced < premium
# in/out = $/M tokens (informational: shown on the card, drives tier fallback)
MODEL_PROFILES: list[dict] = [
    # --- local / small ---
    {"match": "qwen3.5:4b",  "ctx": 32768,  "tier": "fast",     "in": 0.0, "out": 0.0},
    {"match": "nomic",       "ctx": 8192,   "tier": "fast",     "in": 0.0, "out": 0.0},
    {"match": "llama",       "ctx": 131072, "tier": "fast",     "in": 0.0, "out": 0.0},
    {"match": "mistral",     "ctx": 131072, "tier": "fast",     "in": 0.0, "out": 0.0},
    # --- flash / mini / fast classes ---
    {"match": "flash-lite",  "ctx": 1048576, "tier": "fast",    "in": 0.10, "out": 0.40},
    {"match": "flash",       "ctx": 1048576, "tier": "fast",    "in": 0.30, "out": 2.50},
    {"match": "glm-5.3-flash", "ctx": 1048576, "tier": "fast",  "in": 0.075, "out": 0.25},
    {"match": "gpt-5-mini",  "ctx": 400000, "tier": "fast",     "in": 0.25, "out": 2.0},
    {"match": "gpt-5-nano",  "ctx": 400000, "tier": "fast",     "in": 0.10, "out": 0.40},
    {"match": "glm-4.6",     "ctx": 200000, "tier": "balanced", "in": 0.6, "out": 2.5},
    {"match": "haiku",       "ctx": 200000, "tier": "fast",     "in": 1.0, "out": 5.0},
    # --- mid / balanced ---
    {"match": "glm-5.3",     "ctx": 202754, "tier": "balanced", "in": 1.4, "out": 5.6},
    {"match": "sonnet",      "ctx": 1000000, "tier": "balanced", "in": 3.0, "out": 15.0},
    {"match": "gemini-3-pro", "ctx": 1048576, "tier": "balanced", "in": 1.25, "out": 10.0},
    {"match": "gemini-2.5-pro", "ctx": 1048576, "tier": "balanced", "in": 1.25, "out": 10.0},
    {"match": "gpt-5",      "ctx": 400000, "tier": "premium",  "in": 1.25, "out": 10.0},
    # --- premium / reasoning ---
    {"match": "opus",        "ctx": 1000000, "tier": "premium", "in": 5.0, "out": 25.0},
    {"match": "fable",       "ctx": 1000000, "tier": "premium", "in": 5.0, "out": 25.0},
    {"match": "o3",          "ctx": 200000, "tier": "premium",  "in": 2.0, "out": 8.0},
    {"match": "o4",          "ctx": 200000, "tier": "premium",  "in": 2.0, "out": 8.0},
    # --- default: unknown model, be conservative ---
    {"match": "*",           "ctx": 200000, "tier": "balanced", "in": 2.0, "out": 10.0},
]

DEFAULT_PROFILE = MODEL_PROFILES[-1]

SESSION_MODEL_ENV = (
    "TOOLR_MODEL",           # explicit override (pins tests / operators)
    "ANTHROPIC_MODEL",       # Claude Code (verified in hook env)
    "CLAUDE_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "HERMES_MODEL",          # Hermes gateway
    "OPENCODE_MODEL",
    "GEMINI_MODEL",
    "AGY_MODEL",
)


def session_model() -> str | None:
    """The harness's current model from the environment, else None."""
    for var in SESSION_MODEL_ENV:
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return None


def _load_overrides() -> dict:
    try:
        return json.loads(OVERRIDES_PATH.read_text()) or {}
    except (OSError, ValueError):
        return {}


def resolve(model: str | None = None) -> dict:
    """Profile dict for `model` (falls back to the session model, then default).

    Override file entries are matched by substring against `model`, most
    specific (longest key) first.
    """
    model = (model or session_model() or "").strip()
    overrides = _load_overrides()
    for key in sorted(overrides, key=len, reverse=True):
        k = key.lower()
        if k != "*" and k in model.lower():
            prof = dict(overrides[key])
            prof.setdefault("match", key)
            prof.setdefault("tier", "balanced")
            prof.setdefault("ctx", 200000)
            prof.setdefault("in", 0.0)
            prof.setdefault("out", 0.0)
            prof["source"] = "override"
            return prof
    m = model.lower()
    for prof in MODEL_PROFILES:
        key = prof["match"].lower()
        if key == "*" or key in m:
            out = dict(prof)
            out["model"] = model or "(unknown)"
            out["source"] = "builtin"
            return out
    out = dict(DEFAULT_PROFILE)
    out["model"] = model or "(unknown)"
    out["source"] = "builtin-default"
    return out


# ---------------------------------------------------------------- decision

TIER_CEILING = {"fast": 12, "balanced": 10, "premium": 10}
CTX_FLOORS = [(32768, 4), (65536, 6), (131072, 8)]   # ctx<=key -> picks


def decision(profile: dict, user_n: int | None = None, cfg: dict | None = None) -> dict:
    """Card-size decision from the model profile + user's explicit N.

    Priority (matches pipeline.run's own precedence, so the two cannot
    disagree): explicit user count > config decision.max_picks >
    model-derived. The derived count is a CEILING on the card, not a floor.
    """
    cfg = (cfg or {})
    dec = (cfg.get("decision") or {})
    if user_n is not None:
        n = user_n
    elif dec.get("max_picks"):
        n = int(dec["max_picks"])
    elif os.environ.get("TOOLR_MAX_PICKS"):
        n = int(os.environ["TOOLR_MAX_PICKS"])
    else:
        ctx = int(profile.get("ctx") or 200000)
        tier = profile.get("tier", "balanced")
        n = TIER_CEILING.get(tier, 8)
        for floor, picks in CTX_FLOORS:
            if ctx <= floor:
                n = min(n, picks)
                break
    n = max(1, min(n, 50))
    return {
        "max_picks": n,
        "model": profile.get("model", "(unknown)"),
        "tier": profile.get("tier", "balanced"),
        "ctx": profile.get("ctx"),
        "in_cost": profile.get("in"),
        "out_cost": profile.get("out"),
        "source": profile.get("source"),
        # surfaced on the card as one line
        "line": (f"model {profile.get('model', '?')} · {profile.get('tier', '?')} tier · "
                 f"ctx {int(profile.get('ctx') or 0):,}" +
                 (f" · cap {n} picks (explicit)" if user_n is not None else f" · {n} picks")),
    }


# ------------------------------------------------------------ optional data
def refresh(timeout_s: float = _TIMEOUT_S) -> dict:
    """Pull pricing/context from OpenRouter's public models endpoint into the
    override file. Explicitly OFF the hot path — run once, then read locally."""
    body = json.dumps({"User-Agent": "toolr/1.0"}).encode()
    req = urllib.request.Request(OPENROUTER_MODELS_URL, headers={
        "Accept": "application/json", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        data = json.load(resp)
    out = {}
    for m in data.get("data", []):
        mid = m.get("id") or ""
        pricing = m.get("pricing") or {}
        ctx = m.get("context_length")
        if not mid or not ctx:
            continue
        try:
            in_cost = round(float(pricing.get("prompt", 0)) * 1e6, 4)
            out_cost = round(float(pricing.get("completion", 0)) * 1e6, 4)
        except (TypeError, ValueError):
            in_cost, out_cost = 0.0, 0.0
        # keep the entry minimal so the file stays human-editable
        out[mid] = {"ctx": ctx, "in": in_cost, "out": out_cost}
    ov = _load_overrides()
    ov.update(out)
    CACHE_PATH.write_text(json.dumps({"refreshed_at": int(time.time()),
                                      "n": len(out)}))
    OVERRIDES_PATH.write_text(json.dumps(ov, indent=1, sort_keys=True))
    return {"refreshed": len(out), "written": str(OVERRIDES_PATH)}


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    if args and args[0] == "refresh":
        print(json.dumps(refresh()))
    else:
        p = resolve(args[0] if args else None)
        d = decision(p, user_n=int(args[1]) if len(args) > 1 else None)
        print(json.dumps(d, indent=1))
