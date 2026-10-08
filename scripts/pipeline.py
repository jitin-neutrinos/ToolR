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
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import router_core as rc  # noqa: E402

DEFAULT_N = 10
MAX_N = 50

# Wall-clock budget for the whole hook (ms). The card must land inside the
# harness hook timeout or it is discarded silently — that is what the 10 s
# Claude timeout used to eat. Budget is enforced between stages, never by
# killing work mid-flight, so the card is always coherent.
#
# Measured on an idle box: bm25 4 ms + dense 420 ms + laya 770 ms + rewrite
# 2.0 s = ~3.2 s for a full cold route. 5 s leaves headroom for a slow embed
# without ever hitting the hook timeout.
DEFAULT_BUDGET_MS = 5000

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
        ranked = rc.fuse(ranked, dense_hashes, index,
                         alpha=cfg.get("fusion_alpha"), prompt=prompt)
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


def _top_combined(ranked: list[dict], cfg: dict, n: int,
                   topup: bool = False) -> list[dict]:
    """Top-N across all capability kinds with a per-kind quota, so MCPs/
    agents/commands get guaranteed slots instead of skill leftovers.

    topup=True honours the count contract (an explicitly requested N must
    yield N picks, topping up with sub-floor rows). It is OFF by default and
    must stay off for the default-10 path: on the hook path nobody asked for
    N, so the min_score floor is the answer, and a prompt naming no routable
    capability has to abstain rather than be handed ten weak picks
    (regression: 'what is the meaning of life ... bowline knot' scored 0.25
    against a 0.28 floor and the top-up still emitted it — see
    eval/robustness.py::t_no_capability_abstains)."""
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
    # Quota reserves slots for NON-SKILL kinds only — that is the contract the
    # card's coverage promise makes ("MCPs/agents/commands get guaranteed
    # slots"). Splitting skills/others by the tail CUT (below) leaks sub-cut
    # SKILL rows into `others`, and when a dominant leader raises the cut,
    # every other skill lands there: the quota pass then filled ALL slots with
    # them and the fill loop never ran. Measured 2026-10-08: BM25 #1 at 1.87
    # (cut 1.029, 40+ skills sub-cut) vanished from the card entirely while
    # 0.915 rendered on top. Quota on skill-kind rows is the fill loop's job,
    # best-score-first.
    SKILL_KINDS = ("skill", "plugin-skill")
    # Quota must never crowd the score leader out: with a small depth n (the
    # complexity layer hands 4 to a "simple" prompt) and >=2 noise kinds, the
    # quota pass filled ALL n slots and the #1 row (3.73, measured
    # 2026-10-08: 'remove the background from a photo' routing clean_gone
    # 0.72 on top) vanished from the card. Reserve one slot for the best
    # skill whenever one cleared the floor — the fill loop spends it
    # best-score-first, so the leader takes it.
    quota_cap = n - (1 if any(r["kind"] in SKILL_KINDS for r in skills) else 0)
    if quota:
        per_kind: dict[str, list[dict]] = {}
        for r in others:
            if r["kind"] in SKILL_KINDS:
                continue
            per_kind.setdefault(r["kind"], []).append(r)
        for rs in per_kind.values():
            for r in rs[:quota]:
                if len(out) >= max(quota_cap, 0):
                    break
                out.append(r)
                seen_keys.add((r["kind"], r["name"]))
            if len(out) >= max(quota_cap, 0):
                break
    for r in skills + others:              # fill the rest, best score first
        if len(out) >= n:
            break
        key = (r["kind"], r["name"])
        if key not in seen_keys:
            out.append(r)
            seen_keys.add(key)
    # Top-up: an EXPLICIT request for N must yield N when the corpus has N
    # candidates. Measured 2026-10-05: "top 20 skills" returned 19 because the
    # tail cut and the min_score floor removed real candidates. Under-filling
    # is worse than admitting a weak match — the caller asked for a count, so
    # give it, and the card already shows the score so a weak tail pick is
    # visible as such.
    #
    # topup is deliberately opt-in. When nobody named a count (the default-10
    # hook path) the floor is the contract and abstention wins: topping up
    # there turned "no routable capability" into ten sub-floor picks, which
    # is the bug eval/robustness.py::t_no_capability_abstains pins.
    if topup and len(out) < n and ranked:
        for r in ranked:
            if len(out) >= n:
                break
            key = (r["kind"], r["name"])
            if key not in seen_keys:
                out.append(r)
                seen_keys.add(key)
    # Restore the score-order invariant before returning. The quota pass above
    # inserts best-per-kind FIRST, appending the score leader behind quota
    # rows — and every downstream consumer (_score_cliff's largest-relative-
    # drop walk, diversity()'s "rows must be in score order" contract) assumes
    # fused-score order. Measured 2026-10-08: a dominant BM25 #1 (1.52, six
    # token hits incl. the verbatim 'cprofile') landed behind two quota picks,
    # the cliff stage cut the artificial 55% drop, and the leader vanished
    # from the card while rendering 0.636 on top. Quota is a membership
    # policy; score order is the ordering contract. Both hold now.
    out.sort(key=lambda r: -float(r.get("score", 0.0)))
    return out[:n]


def _route_ctx(picks: list[dict]) -> str:
    if not picks:
        return "no specialized capability matched"
    return ", ".join(r["name"] for r in picks[:4])


def _adoption_adjust(depth: int) -> int:
    """Layer 3: 24h adoption data adjusts the depth (BoR-lite prior).

    eval/adoption.jsonl carries daily harvest rows (routed prompts and whether
    a Skill load followed within 5 min). While <24h of data exists this is a
    no-op (data collection started 2026-10-06; input flows from 2026-10-07).
    Once ripe: high loaded-rate -> widen by 1 (the shortlist is being used);
    low rate -> tighten by 1 (suggestions are being ignored; fewer, sharper).
    Never adjusts below 3 or above 12.
    """
    try:
        path = Path("/home/notjitin/Work/tool-router/eval/adoption.jsonl")
        import json as _json
        rows = [_json.loads(l) for l in path.read_text().splitlines() if l.strip()]
        if not rows:
            return depth
        span = max(r["ts"] for r in rows) - min(r["ts"] for r in rows)
        if span < 86400:      # less than 24h collected — no input yet
            return depth
        rate = sum(r.get("loaded_after", 0) for r in rows) / len(rows)
        if rate >= 0.5:
            return min(depth + 1, 12)
        if rate < 0.2:
            return max(depth - 1, 3)
        return depth
    except Exception:
        return depth


def _complexity(prompt: str, sc: dict | None = None) -> int:
    """Layer 1a: query complexity -> card depth (Adaptive-RAG shape).

    Zero-ML complexity signal: intents (scaffold's pattern extraction), query
    length and multi-part naming. simple / standard / multi-signal.
    Returns the depth: 4 / 7 / 10.
    """
    try:
        sc = sc or rc.scaffold(prompt)
    except Exception:
        sc = {"intents": [], "words": len((prompt or "").split())}
    intents = len(set(sc.get("intents") or []))
    words = int(sc.get("words") or 0)
    parts = len(rc._PATH_RE.findall(prompt or "")) if hasattr(rc, "_PATH_RE") else 0
    low = (prompt or "").lower()
    multi = sum(low.count(w) > 0 for w in (" and ", " then ", " also ", " plus "))
    score = 0
    score += 1 if intents >= 2 else 0
    score += 1 if intents >= 3 else 0
    score += 1 if words > 25 else 0
    score += 1 if parts >= 2 else 0
    score += 1 if multi >= 1 else 0
    if score >= 3:
        return 10
    if score >= 1:
        return 7
    return 4


def _score_cliff(ranked: list[dict], base: float = 0.0) -> float:
    """Layer 1b: score-cliff truncation (MagicSelector shape).

    The cut is not a fixed ratio of the leader — it is where relevance
    actually falls. Walk the ranked list; the cliff is the largest RELATIVE
    drop between adjacent picks. Returns the score at the cliff.
    """
    if len(ranked) < 3:
        return 0.0
    best_drop, cliff_score = 0.0, 0.0
    for i in range(1, min(len(ranked), 15)):
        prev, cur = float(ranked[i-1].get("score", 0)), float(ranked[i].get("score", 0))
        if prev <= 0.0:
            continue
        drop = (prev - cur) / prev
        if drop > best_drop and drop > 0.35:      # a real cliff, not decay noise
            best_drop, cliff_score = drop, cur
    return cliff_score


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
        rewrite: bool = True, n_override: int | None = None) -> dict:
    """Full pipeline. Returns {card, picks, rewritten, n, provider}.

    rewrite=False skips stage 2/3 (LLM prompt-engineer): routing only, no LLM call."""
    cfg = cfg or rc.load_config()
    index = index or rc.load_index() or rc.build_index(cwd)
    from pathlib import Path as _P
    dense_path = rc.index_path().parent / (rc.index_path().stem + ".dense.npz")
    stack = rc.stack_tokens(cwd)
    # Explicit --top N beats a number named in the prompt, which beats config.
    # asked_for_count records whether the COUNT came from the user (flag or
    # prompt text) rather than from the default — that is what turns the
    # sub-floor top-up on. cfg["top_n"] is a default, not a request.
    prompt_n = user_n(prompt)
    if n_override is not None:
        n = max(1, min(int(n_override), MAX_N))
        asked_for_count = True
    elif prompt_n is not None:
        n = prompt_n
        asked_for_count = True
    else:
        n = int(cfg.get("top_n", DEFAULT_N))
        asked_for_count = False
    # Model-aware card size + authority check (both fail-open, ~ms).
    model_decision = None
    try:
        import modelcontext as mc
        profile = mc.resolve()
        model_decision = mc.decision(profile, user_n=n if asked_for_count else None, cfg=cfg)
        # Cap the default path at the model-derived count; an explicit user
        # count always wins (decision() already returns it unchanged).
        n = max(1, min(n, model_decision["max_picks"])
                if not asked_for_count else n)
    except Exception:
        pass  # resolver must never break routing
    authority_ok = True
    authority_note = ""
    try:
        import sys as _sys
        from pathlib import Path as _Path
        _root = str(_Path(__file__).resolve().parent.parent)
        if _root not in _sys.path:
            _sys.path.insert(0, _root)
        import authority as _auth
        heal = _auth.selfheal()
        authority_ok = bool(heal.get("mandate_ok")) and heal.get("shim_ok") is not False
        if not heal.get("mandate_ok"):
            authority_note = _auth.card_notice()
        elif heal.get("repaired"):
            authority_note = ("_Toutur authority self-heal re-applied: "
                              + ", ".join(heal["repaired"])
                              + " (an update or healing pass had undone it). _")
        elif heal.get("shim_ok") is False:
            authority_note = ("_Toutur interception shim on harness '"
                              + str(heal.get("harness") or "?")
                              + "' missing and not auto-repairable — run "
                              "install.py. Routing still served this card._")
    except Exception:
        pass
    t0 = time.monotonic()

    def elapsed_ms() -> float:
        return (time.monotonic() - t0) * 1000.0

    def budget_left() -> float:
        return float(cfg.get("budget_ms", DEFAULT_BUDGET_MS)) - elapsed_ms()

    # Stage 1 — first-pass route
    ranked1 = _route_once(prompt, index, stack, cfg, dense_path, n)
    # Layer 1: the depth n follows the query's complexity, not a fixed number.
    # An explicit user count (--top / "top 20") still wins outright.
    if not asked_for_count:
        n = _adoption_adjust(_complexity(prompt))
        if model_decision and model_decision.get("max_picks"):
            n = min(n, int(model_decision["max_picks"]))
    picks1_raw = _top_combined(ranked1, cfg, n, topup=asked_for_count)
    # Layer 1b: score-cliff truncation — cut where relevance actually falls.
    if not asked_for_count:
        cliff = _score_cliff(picks1_raw)
        if cliff > 0:
            picks1_raw = [r for r in picks1_raw if float(r["score"]) >= cliff]
    # Layer 2: diversity — drop picks redundant with an already-kept pick.
    # Skipped when the user asked for an explicit count: the count contract
    # (top N must return N) outranks diversity; the card prints scores, so
    # weak/redundant tail picks are still visible for what they are.
    picks1 = picks1_raw
    if not asked_for_count:
        try:
            import diversity as _dv
            q_toks = set(rc.tokenize(prompt))
            picks1 = _dv.diversify(picks1_raw, q_toks)
        except Exception:
            pass

    # Stage 2 — PROMPT REWRITER: REMOVED (owner decision 2026-10-05).
    #
    # The hook now routes the user's own words and nothing else. The rewriter
    # was the only reason the hook spent real time: it made a network call on
    # every single message, through a free tier that (measured 2026-10-05)
    # returns HTTP 200 with an EMPTY body roughly 1 call in 5, and whose
    # "fast" models queue behind 10 s of work. Even with a deadline, a breaker
    # and a retry, the median route was 2.5-5.5 s with 15-18 s outliers, and a
    # 5 s hook budget could not be held.
    #
    # What the rewriter added was a "Use:" line naming the picks. The card
    # ALREADY does that, better and for free: the pick list is the same list,
    # with scores and match reasons, and it ships whether or not any model
    # answered. rewriter.py stays on disk (unused, importable, still tested) so
    # nothing measured there is lost if it is ever wanted back.
    rewritten, provider = None, None

    # Stage 3 — re-route the rewritten prompt. The rerank runs ONCE per route:
    # stage 1 already applied it to the original prompt, and a second 4 s CPU
    # call on a lightly reworded prompt bought nothing measurable (measured
    # 2026-10-05: identical picks, ~4 s saved).
    # Stage 5 (gap tracking + sourcing tiers remain below) — uses merged_picks/n
    merged_picks = picks1
    if rewritten:
        if budget_left() > 400:
            ranked2 = _route_once(rewritten, index, stack, cfg, dense_path, n,
                                  rerank=False)
            picks2 = _top_combined(ranked2, cfg, n, topup=asked_for_count)
            # Stage 4 — union, first-pass order for stable names, second-pass additions after
            seen = {(r.get("kind"), r.get("name")) for r in picks1}
            merged_picks = (picks1 + [r for r in picks2
                                      if (r.get("kind"), r.get("name")) not in seen])[:n]
        else:
            provider = f"{provider} (re-route skipped: out of time budget)"

    card = rc.render_pipeline_card(prompt, rewritten, provider, merged_picks,
                                   picks1, n, stack, cfg, index.get("stats", {}))
    # One-line model context + authority status appended (cheap, informative).
    if model_decision:
        card += f"\n\n_Model: {model_decision['line']}_"
    if authority_note:
        card += f"\n\n{authority_note}"
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
            card += (f"\n\n**Oracle: no local capability serves this.** The "
                     f"interactive skill finder can shop skills.sh + the MCP "
                     f"registry — search, inspect the real SKILL.md body, "
                     f"injection-screen, install pinned to the reviewed commit, "
                     f"all gated on your approval: "
                     f"`~/.tool-router/route --finder \"{prompt[:60]}\"`")
    except Exception:
        pass  # sourcing must never break routing

    return {"card": card, "picks": names, "rewritten": rewritten,
            "provider": provider, "n": n}
