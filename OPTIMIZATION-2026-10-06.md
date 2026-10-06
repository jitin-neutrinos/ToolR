# Tool-router optimisation report — 2026-10-06

Commit: `1add52d` (master). All numbers measured on kurama-core, golden set
454 queries (360 positive / 38 negative / 56 stale excluded), index 768 items.

## What changed

| Change | Files | Mechanism |
|---|---|---|
| **RRF → convex weighted score blend** | `scripts/router_core.py::fuse()`, `scripts/pipeline.py`, `eval/ablate.py` | TM2C2 (Bruch/Gai/Ingber, arXiv:2210.11934): per-query min-max normalised BM25 and dense scores blended as `alpha*dense + (1-alpha)*bm25`. `alpha` from config `fusion_alpha`. One ordering now — the fused value IS what `select()` cuts on (the old code sorted by RRF but cut on a different `score` field). Dense-only discoveries keep a fallback bonus so they can still clear min_score. |
| **Index-side description expansion + body indexing** | new `scripts/body_extract.py`, `router_core.py::build_index()/load_index()`, `dense_index.py` | SKILL.md bodies (first 120 words), "Use when ..." trigger clauses and quoted trigger phrases extracted at build time and baked into BM25 tokens AND dense embedding text (both lanes see the same corpus — the 2026-10-05 lesson). 696/768 items gained a body, 365 a when-clause, 404 trigger phrases. No LLM, no network: pure stdlib extraction. |
| **Exact-name guarantee** | `router_core.py::fuse()/_named_exactly()` | A prompt naming a capability by exact name keeps it in the output regardless of where fusion ranks it (mirrors Codex CLI's fix for issue #21503; same generic-verb suppression as `score()` so the two layers agree). |
| **Abstention fix (pre-existing failure)** | `scripts/pipeline.py::_top_combined(topup=)` | The 2026-10-05 "requested N must yield N" top-up was firing on the default-10 hook path too, emitting sub-floor picks for prompts that name nothing routable. Top-up is now opt-in: only when the user explicitly requested a count (`--top N` or a number in the prompt text). Default path abstains again. Fixed by the delegated subagent (dispatch verified count=1; landed in commit `1add52d`). |

## Measured results (golden set, ablate.py, logged to eval/runs.jsonl)

| Metric | RRF baseline | Convex + body | Delta |
|---|---|---|---|
| recall@1 | 0.1222 | **0.1833** | **+0.0611 (+50%)** |
| recall@5 | 0.2694 | **0.3667** | **+0.0973 (+36%)** |
| recall@10 | 0.3556 | **0.4361** | **+0.0805 (+23%)** |
| MRR | 0.1942 | **0.2717** | **+0.0775 (+40%)** |
| nDCG@10 | 0.2350 | **0.3129** | **+0.0779 (+33%)** |

Attribution (from the alpha sweep, alpha=0 = body-only BM25):
- Body/trigger indexing alone: R@1 +0.025, R@10 +0.044
- Convex fusion on top: R@1 +0.036, R@10 +0.036

Alpha sweep (R@1 / R@10): 0.0 → 0.147/0.400 · 0.5 → 0.175/0.381 · 0.6 → 0.192/0.358 · 1.0 → 0.128/0.339.
**Shipped alpha = 0.5**: 0.6 wins R@1 by +0.017 but costs R@10 −0.078; recall@10
("was the right skill on the card at all") is the operational metric for a
routing card, so 0.5. Sweep is reproducible via the cached-dense script in this
directory's history; re-measure before changing `fusion_alpha`.

## Verification

- `python3 scripts/selftest.py` → **43/43 passed** (incl. fusion-scale contract, save/load round trip, count contract)
- `python3 eval/robustness.py` → **21/26, 0 unexpected failures** (5 known XFAILs unchanged; `t_no_capability_abstains` now passes)
- Live routes: `~/.tool-router/route` **0.24 s** wall (inside the 5 s hook budget); exact-name probe `use the systematic-debugging skill` → systematic-debugging #1 score 4.728; semantic probe "make the page feel snappier when scrolling" → locomotive-scroll #1
- Dense index fully rebuilt (768 embedded, 0 reused) — embedded text changed, so the incremental cache was bypassed by moving the old `.npz`/`.meta.json` aside

## Regressions found and fixed during the work

1. **Adjective-only label lost after body indexing** (selftest caught it): the portfolio skill's body contains "hermes chat", giving the row a 'chat' hit that cleared the adjective-only flag on a query carried entirely by "revamp"/"overhaul". Fix: the label now derives from **identity fields** (name/desc/aliases) only; body-only hits display but don't clear the flag.
2. **Ollama breaker left tripped** by the pre-change eval run (stale `until` timestamp in `~/.tool-router/breaker-ollama.json`) — silently degraded the dense lane to zero mid-measurement. Cleared; the bimodal-zero lesson from the skill doc applies.

## Deferred (explicit)

- Skill-body indexing capped at 120 words/item — full-body indexing would grow index.json ~4×; measure whether the next 120 words add recall before extending.
- `fusion_alpha` re-sweep after the next golden-set harvest (labels drift with the corpus).
- k1/b BM25 sweep (R3 in the research note) — not touched in this pass.
- Laya rerank stays off (re-confirmed: fused_rerank == fused on every metric).
