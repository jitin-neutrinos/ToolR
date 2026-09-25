#!/usr/bin/env python3
"""rewriter.py — Stage 2 of the tool-router pipeline: the prompt engineer.

Takes the user's raw prompt plus the first-pass routing card context and asks
an LLM to rewrite the prompt into a structured, richer form (ReAct-shaped:
Goal / Target / Done-condition / Steps / Verify), without inventing scope the
user did not ask for. The rewritten prompt is re-routed (Stage 3) and the two
pick-sets are merged, so the final card covers both the user's words and the
enriched intent.

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
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

GEMINI_URL = ("https://generativelanguage.googleapis.com/v1beta/openai/"
              "chat/completions")
GEMINI_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest")

SYSTEM = (
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


def _load_config(base_cfg: dict) -> dict:
    cfg = {"enabled": True, "timeout_s": 12.0, "max_tokens": 700,
           "max_chars": 4000, "min_chars": 12}
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


def _post(url: str, key: str, body: dict, timeout: float) -> str | None:
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


def rewrite(prompt: str, route_ctx: str, base_cfg: dict) -> dict | None:
    """Return {"prompt": <rewritten>, "provider": <name>} or None (fail-open).

    `route_ctx` is the one-line first-pass top-pick context handed to the
    rewriter so the rewrite can name the capability it likely needs — never
    shown to the user, only used to re-route.
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

    user = (f"First-pass routing suggested: {route_ctx}\n\n"
            f"User request:\n\"\"\"\n{p}\n\"\"\"")

    # 1) gemini — flash first, lite as the high-demand fallback
    key = _key(("GOOGLE_API_KEY", "GEMINI_API_KEY"))
    if key:
        for model in GEMINI_MODELS:
            out = _post(GEMINI_URL, key,
                        {"model": model,
                         "messages": [{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": user}],
                         "maxTokens": int(cfg["max_tokens"]),
                         "temperature": 0.2},
                        float(cfg["timeout_s"]))
            if out and out.strip():
                return {"prompt": out.strip()[:4000], "provider": model}
        # every model failed (auth or persistent 503) — trip the breaker so
        # following prompts skip the ~12s wait until it expires
        _trip_breaker(120.0)
        return None

    # 2) custom OpenAI-compatible
    base = os.environ.get("ROUTER_REWRITE_BASE_URL")
    ckey = os.environ.get("ROUTER_REWRITE_API_KEY")
    model = os.environ.get("ROUTER_REWRITE_MODEL")
    if base and ckey and model:
        out = _post(base.rstrip("/") + "/chat/completions", ckey,
                    {"model": model,
                     "messages": [{"role": "system", "content": SYSTEM},
                                  {"role": "user", "content": user}],
                     "max_tokens": int(cfg["max_tokens"]),
                     "temperature": 0.2},
                    float(cfg["timeout_s"]))
        if out and out.strip():
            return {"prompt": out.strip()[:4000], "provider": model}
    _trip_breaker(300.0)
    return None
