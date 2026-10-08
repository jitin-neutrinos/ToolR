"""tool-router — the routing pipeline on every incoming gateway message.

Intercept point: pre_gateway_dispatch (fires for every MessageEvent on every
platform, before auth/dispatch). Pipeline per message:

    1. route the prompt               -> top-N combined picks
    2. (removed 2026-10-05) the prompt-engineer LLM stage. The hook now routes
       the user's own words only: the rewrite made a network call on every
       message through a free tier that returns an empty body ~1 call in 5, and
       it was the sole reason the hook exceeded its latency budget. The card's
       pick list already names the capabilities to load, so nothing was lost.

The router card rides along as a trailing block and the message text is left
untouched. Every stage fails open: router down -> message untouched. A 90s
per-session result cache stops repeat calls for identical texts.

Config (config.yaml, all optional):
  tool_router:
    disabled: false
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time

ROUTER_HOME = os.path.expanduser("~/.tool-router")
ROUTES = [
    os.path.expanduser("~/Work/tool-router/scripts"),
    os.path.expanduser("~/.hermes/skills/tools/tool-router/scripts"),
]
for _p in ROUTES:
    if os.path.isdir(_p):
        sys.path.insert(0, _p)
        break

_CACHE_MAX = 200


def _cfg_disabled() -> bool:
    """config.yaml: tool_router.disabled: true silences the interceptor."""
    try:
        import yaml
        home = os.path.expanduser(
            os.environ.get("HERMES_HOME", "~/.hermes"))
        cfg = yaml.safe_load(open(os.path.join(home, "config.yaml"))) or {}
        v = (cfg.get("tool_router") or {})
        return bool(v.get("disabled")) if isinstance(v, dict) else False
    except Exception:
        return False


def _cache_load() -> dict:
    try:
        return json.loads(os.path.join(ROUTER_HOME, "gateway-cache.json") and
                          open(os.path.join(ROUTER_HOME, "gateway-cache.json")).read())
    except (OSError, ValueError):
        return {}


def _cache_save(cache: dict) -> None:
    try:
        if len(cache) > _CACHE_MAX:
            keep = sorted(cache.items(), key=lambda kv: -kv[1]["at"])[: _CACHE_MAX // 2]
            cache = dict(keep)
        with open(os.path.join(ROUTER_HOME, "gateway-cache.json"), "w") as fh:
            json.dump(cache, fh)
    except OSError:
        pass


def _pipeline_result(text: str) -> dict | None:
    """Run the pipeline. Fail-open: any problem -> None.

    rewrite=False is explicit rather than relying on the stage being inert: the
    gateway hook is on the critical path of every message, and a future edit
    that re-enables the rewriter must not silently put a network call back in
    it.
    """
    try:
        import router_core as rc
        import pipeline
        cfg = rc.load_config()
        result = pipeline.run(text, rc._route_cwd(), cfg, rewrite=False)
        return result if result and result.get("card") else None
    except Exception:
        return None


def _card_to_canvas(result: dict) -> str | None:
    """Render the routing result as an Astra canvas block: the UI shows it as
    a detailed steps/table surface instead of raw prose. The model keeps
    following the same contract — this is display-only. Fail-open."""
    try:
        import re as _re
        card = result.get("card") or ""
        # parse the markdown card back into rows
        rows = _re.findall(
            r"- `([^`]+)` — ([^(\n]+) \(score ([0-9.]+), matched: ([^)]*)\)", card)
        if not rows:
            return None
        blocks = [
            {"type": "callout", "tone": "info", "title": "Toutur — capabilities routed",
             "body": "The router picked these before work started; the agent "
                     "will load them as needed. Nothing is required from you."},
            {"type": "table", "columns": ["capability", "kind", "score", "matched"],
             "rows": [[n.strip(), k.replace("Skill (plugin)", "plugin skill").strip(),
                       s, m] for n, k, s, m in rows]},
        ]
        ml = _re.search(r"_Model: (.+)_", card)
        if ml:
            blocks.append({"type": "callout", "tone": "info",
                           "body": "Decision context: " + ml.group(1)})
        cov = _re.search(r"\*\*Coverage:\*\* ([^\n]+)", card)
        if cov:
            blocks.append({"type": "callout", "tone": "info",
                           "body": "Coverage: " + cov.group(1).strip()})
        return "```astra-canvas\n" + json.dumps({"v": 1, "title": "Toutur routing",
                                                 "blocks": blocks}) + "\n```"
    except Exception:
        return None


def pre_gateway_dispatch(event=None, gateway=None, session_store=None, **kwargs):
    """Return {"action": "rewrite", "text": original + router card} so every
    dispatched message carries the routing pipeline's output. Return None
    (allow, untouched) for bypass shapes, cache hits, or any failure — a
    router must never break or delay a message it cannot help."""
    if _cfg_disabled():
        return None
    # Export the session's model into the environment so modelcontext.py's
    # env scan (the cheapest source) finds it before the state.db fallback.
    # The gateway knows the model; the hook subprocess would not otherwise
    # inherit it (verified 2026-10-06: hook env carried no MODEL var).
    try:
        model = getattr(gateway, "model", None) or getattr(
            getattr(gateway, "session", None), "model", None)
        if model:
            os.environ.setdefault("HERMES_MODEL", str(model))
    except Exception:
        pass
    text = getattr(event, "text", "") or ""
    if len(text) < 12:
        return None
    stripped = text.lstrip()
    if stripped.startswith(("/", "*", "#")):
        return None  # slash command / documented bypass prefixes
    if "[ROUTED" in text:  # already routed (cron relays, retries)
        return None

    key = hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]
    cache = _cache_load()
    now = time.time()
    hit = cache.get(key)
    if hit and now - hit.get("at", 0) < 90:
        card = hit.get("card")
    else:
        result = _pipeline_result(text)
        card = result.get("card") if result else None
        if card:
            cache[key] = {"at": now, "card": card}
            _cache_save(cache)
    if not card:
        return None
    # Astra surfaces (web/android): render the routing as a canvas surface —
    # detailed steps + picks table with confidence scores. The model still
    # receives the full markdown card inside the appended block; the canvas
    # is what the USER sees. Other surfaces (terminal, telegram) keep prose.
    payload = text + "\n\n[ROUTED CONTEXT — follow the card]\n" + card
    surface = ""
    try:
        surface = str(getattr(event, "surface", "") or "")
    except Exception:
        surface = ""
    if surface.startswith(("webui", "android")) or surface in ("web", "astra"):
        canvas = _card_to_canvas(result) if result else None
        if canvas:
            payload = payload + "\n\n" + canvas
    return {"action": "rewrite", "text": payload}


def register(ctx):
    """Hermes plugin entry point — discovered only via the sibling plugin.yaml."""
    ctx.register_hook("pre_gateway_dispatch", pre_gateway_dispatch)
