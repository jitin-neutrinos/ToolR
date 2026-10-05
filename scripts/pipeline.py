#!/usr/bin/env python3
"""pipeline.py — the full four-stage routing pipeline.

    Stage 1  route the user's prompt            -> top-N picks (skills/MCPs/agents/plugins)
    Stage 2  prompt engineer rewrites it        -> structured ReAct-shaped prompt
    Stage 3  re-route the rewritten prompt      -> top-N picks again
    Stage 4  verify + merge                     -> union of both pick-sets, capability
                                                 coverage check, final card

N defaults to 10 (combined across kinds). If the user's prompt names a number
("top 20 tools", "5 skills"), their number replaces the default.

The card keeps the model-facing contract of the old single-stage card
(enrich / load / gate) and adds a `## Rewritten prompt` block when Stage 2
produced one, plus a `## Coverage` block naming capability kinds found and
gaps. Every stage fails open: if the rewriter is down or returns nothing the
original prompt routes alone; if the dense lane is down fusion degrades; if
Laya is down order stands.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402

DEFAULT_N = 10
MAX_N = 50

_N_RE = re.compile(
    r"\btop[- ]?(\d{1,3})\b|\b(\d{1,3})\s+(?:top\s+)?(?:skills|tools|mcp|mcps|plugins|agents|commands)",
    re.I)


def user_n(prompt: str) -> int | None:
    """The user's requested pick count, if the prompt names one."""
    m = _N_RE.search(prompt or "")
    if not m:
        return None
    n = int(m.group(1) or m.group(2))
    return max(1, min(n, MAX_N))


def _route_once(prompt: str, index: dict, stack: list[str], cfg: dict,
                dense_path, n: int, rerank: bool = True) -> list[dict]:
    ranked = rc.score(index, prompt, stack, rc.load_learned(), cfg.get("mcp_hints"))
    # dense lane (advisory)
    try:
        import dense_index as di
        dense_hashes = di.dense_rank(prompt, index, dense_path, top_n=50)
        ranked = rc.fuse(ranked, dense_hashes, index)
    except Exception:
        pass  # fusion needs the dense lane; BM25 order stands without it
    # laya rerank (advisory, margin-gated)
    if not rerank:
        return ranked
    try:
        import laya_rerank
        for r in ranked[:12]:
            r.setdefault("description", r.get("desc", ""))
        ranked = laya_rerank.rerank(ranked, prompt, cfg)
    except Exception:
        pass
    return ranked


def _top_combined(ranked: list[dict], cfg: dict, n: int) -> list[dict]:
    """Top-N across all capability kinds with a per-kind quota, so MCPs/
    agents/commands get guaranteed slots instead of skill leftovers."""
    picks = [r for r in ranked if r.get("score", 0) >= float(
        cfg.get("min_score", rc.DEFAULT_CONFIG["min_score"]))]
    if picks:
        cut = picks[0]["score"] * float(cfg.get("tail_ratio", rc.DEFAULT_CONFIG["tail_ratio"]))
    else:
        cut = 0.0
    skills, others = [], []
    for r in picks:
        # tail cut applies to skills only: a dominant skill must not starve
        # other kinds out of their quota slots
        (skills if r["kind"] in ("skill", "plugin-skill") and r["score"] >= cut
         else others).append(r)
    out, seen_keys = [], set()
    quota = max(0, int(cfg.get("kind_quota", rc.DEFAULT_CONFIG["kind_quota"])))
    if quota:                              # reserve best slots per non-skill kind first
        per_kind: dict[str, list[dict]] = {}
        for r in others:                   # already in fused-score order
            per_kind.setdefault(r["kind"], []).append(r)
        for rs in per_kind.values():
            for r in rs[:quota]:
                if len(out) >= n:
                    break
                out.append(r)
                seen_keys.add((r["kind"], r["name"]))
            if len(out) >= n:
                break
    for r in skills + others:              # fill the rest, best score first
        if len(out) >= n:
            break
        key = (r["kind"], r["name"])
        if key not in seen_keys:
            out.append(r)
            seen_keys.add(key)
    return out[:n]


def _route_ctx(picks: list[dict]) -> str:
    if not picks:
        return "no specialized capability matched"
    return ", ".join(r["name"] for r in picks[:4])


def _coverage(picks: list[dict]) -> list[str]:
    kinds = {}
    for r in picks:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    order = ["skill", "plugin-skill", "mcp", "agent", "command",
             "fleet-tool", "fleet-plugin"]
    out = []
    for k in order:
        if kinds.get(k):
            out.append(f"{kinds[k]} {rc.KIND_LABEL.get(k, k).lower()}"
                       + ("s" if kinds[k] > 1 and not k.endswith("skill") else ""))
    return out or ["nothing scored above threshold — proceeding unaided is fine"]


def run(prompt: str, cwd, cfg: dict | None = None, index: dict | None = None,
        rewrite: bool = True) -> dict:
    """Full pipeline. Returns {card, picks, rewritten, n, provider}.

    rewrite=False skips stage 2/3 (LLM prompt-engineer): routing only, no LLM call."""
    cfg = cfg or rc.load_config()
    index = index or rc.load_index() or rc.build_index(cwd)
    from pathlib import Path as _P
    dense_path = rc.index_path().parent / (rc.index_path().stem + ".dense.npz")
    stack = rc.stack_tokens(cwd)
    n = user_n(prompt) or int(cfg.get("top_n", DEFAULT_N))

    # Stage 1 — first-pass route
    ranked1 = _route_once(prompt, index, stack, cfg, dense_path, n)
    picks1 = _top_combined(ranked1, cfg, n)

    # Stage 2 — prompt engineer (fail-open; skipped with rewrite=False)
    rewritten, provider = None, None
    if rewrite:
        try:
            import rewriter
            out = rewriter.rewrite(prompt, _route_ctx(picks1), cfg)
            if out:
                rewritten, provider = out["prompt"], out["provider"]
        except Exception:
            pass

    # Stage 3 — re-route the rewritten prompt. The rerank runs ONCE per route:
    # stage 1 already applied it to the original prompt, and a second 4 s CPU
    # call on a lightly reworded prompt bought nothing measurable (measured
    # 2026-10-05: identical picks, ~4 s saved).
    merged_picks = picks1
    if rewritten:
        ranked2 = _route_once(rewritten, index, stack, cfg, dense_path, n,
                              rerank=False)
        picks2 = _top_combined(ranked2, cfg, n)
        # Stage 4 — union, first-pass order for stable names, second-pass additions after
        seen = {(r.get("kind"), r.get("name")) for r in picks1}
        merged_picks = (picks1 + [r for r in picks2
                                  if (r.get("kind"), r.get("name")) not in seen])[:n]

    card = rc.render_pipeline_card(prompt, rewritten, provider, merged_picks,
                                   picks1, n, stack, cfg, index.get("stats", {}))
    names = [f"{kind}:{name}" for kind, name in
             ((r['kind'], r['name']) for r in merged_picks)]

    # Gap tracking + sourcing tiers (never blocks, never routes). Cheap score
    # floor gates gap RECORDING; the Laya oracle runs only inside the auto
    # tier (a wrong-but-confident match can outscore a correct one — see
    # source.gap_oracle — so unattended installs get the semantic verdict).
    try:
        import gaptrack
        import source as _source
        hit, entry = _source.auto_install(prompt, prompt, cfg, merged_picks)
        if hit and hit.get("ok"):
            card += (f"\n\n**Auto-sourced** (repeated gap, free + screened): "
                     f"installed skill `{hit['installed']}` and reindexed — "
                     f"it will route from the next turn. Say 'undo sourcing' "
                     f"to remove.")
        elif entry.get("served") is True and not hit:
            card += (f"\n\n**Oracle: no local capability serves this.** To search "
                     f"free registries (installed only on your approval): "
                     f"~/.tool-router/route --source \"{prompt[:60]}\"")
    except Exception:
        pass  # sourcing must never break routing

    return {"card": card, "picks": names, "rewritten": rewritten,
            "provider": provider, "n": n}
