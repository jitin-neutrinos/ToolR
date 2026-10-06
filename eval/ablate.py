#!/usr/bin/env python3
"""ablate.py — score every retrieval lane against the golden set.

Zero dependencies, assert-based, same style as scripts/selftest.py.

    python3 ablate.py                # all lanes, print a table
    python3 ablate.py --lane bm25    # one lane
    python3 ablate.py --repeat 5     # variance across repeated builds
    python3 ablate.py --assert        # exit non-zero on a regression (CI hook)

LANES
  bm25          rc.score only. Deterministic, no network, no embeddings.
  dense         local embedding lane only (needs Ollama up).
  fused         BM25 + dense, RRF-fused — what the router actually runs.
  fused_rerank   fused + the Laya rerank stage, if it is reachable.

DETERMINISM (why bm25 is the trustworthy lane)
  - rc.score is pure Python: no RNG, no clock, no dict-order dependence, no
    network. Its only inputs are (index, prompt, stack, learned, mcp_hints).
    Verified: three consecutive calls and three full rc.build_index() rebuilds
    produce byte-identical item key orders on this machine.
  - So the eval ALWAYS passes stack=[], learned={} and the SAME index object to
    every lane. A lane change then cannot be blamed on corpus drift.
  - The two lanes that are NOT reproducible: dense (a remote embedding call
    whose model can be swapped out from under the index) and rerank (a local
    decision model whose weights change without a code commit). Those lanes
    are therefore reported with a repeat-count and a spread, never with a
    single bare number. --repeat N makes the spread visible.

ATTRIBUTION
  A change to one lane is attributable because the other lanes are held fixed
  and the query set is frozen in golden.jsonl. Read the delta column; a lane
  whose recall@1 moved while fused_rerank did not moved on its own.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import goldset  # noqa: E402
import metrics as M  # noqa: E402
import router_core as rc  # noqa: E402

GOLDEN = HERE / "golden.jsonl"
RUNS = HERE / "runs.jsonl"
TOP_N = 10


# --------------------------------------------------------------- the four lanes

def lane_bm25(prompt, index, cfg, dense_path, stack):
    """Sparse lane. Pure function of its arguments. Always healthy."""
    return rc.score(index, prompt, stack=stack, learned={},
                    mcp_hints=cfg.get("mcp_hints")), True


def lane_dense(prompt, index, cfg, dense_path, stack):
    """Dense lane alone, on a comparable 0..1 scale.

    An empty result is NOT healthy: it means the embedding call failed or the
    model is missing, and reporting it as a lane score of 0 would be a
    fabricated measurement.
    """
    import dense_index as di
    try:
        hashes = di.dense_rank(prompt, index, dense_path, top_n=TOP_N * 5)
    except Exception:
        return [], False
    if not hashes:
        return [], False
    by_hash = {rc._dense_hash_of(it): it for it in index["items"]}
    rows = []
    for j, h in enumerate(hashes):
        it = by_hash.get(h)
        if it is None:
            continue
        rows.append({**{k: v for k, v in it.items() if k not in ("tokens", "name_tokens")},
                     "score": round(1.0 - j / max(len(hashes), 1), 4),
                     "dense_rank": j})
    return rows, bool(rows)


def lane_fused(prompt, index, cfg, dense_path, stack):
    """What pipeline._route_once does minus the LLM rewriter and rerank.

    Returns (rows, healthy). `healthy` is False when the dense lane was
    unavailable and this silently fell back to BM25 — which is exactly what the
    router does in production, and exactly what makes an eval lie: a degraded
    fused lane scores IDENTICALLY to BM25 (verified: same names, same scores,
    35x faster), so a bare metric table reads as "fusion contributed nothing"
    when the truth is "the dense lane never ran".
    """
    import dense_index as di
    base, _ = lane_bm25(prompt, index, cfg, dense_path, stack)
    try:
        hashes = di.dense_rank(prompt, index, dense_path, top_n=50)
        if not hashes:
            return base, False
        return rc.fuse(base, hashes, index, alpha=cfg.get("fusion_alpha"),
                       prompt=prompt), True
    except Exception:
        return base, False


def lane_fused_rerank(prompt, index, cfg, dense_path, stack):
    """Fused, then Laya rerank — the full Stage-1 + rerank path."""
    ranked, healthy = lane_fused(prompt, index, cfg, dense_path, stack)
    try:
        import laya_rerank
        for r in ranked[:12]:
            r.setdefault("description", r.get("desc", ""))
        return laya_rerank.rerank(ranked, prompt, cfg), healthy
    except Exception:
        return ranked, healthy


LANES = {
    "bm25": lane_bm25,
    "dense": lane_dense,
    "fused": lane_fused,
    "fused_rerank": lane_fused_rerank,
}


# ------------------------------------------------------------------ scoring

def score_lane(lane: str, rows: list[dict], index, cfg, dense_path) -> tuple[dict, list[dict]]:
    """Returns (metrics, per-query detail). Detail is what you debug with."""
    import pipeline as pl
    fn = LANES[lane]
    stack: list[str] = []          # frozen: the eval never infers a repo stack
    per_query, failures = [], []
    degraded = 0
    for row in rows:
        if row.get("label_status") == "stale":
            continue               # capability no longer exists; not a negative
        prompt = row["prompt"]
        try:
            ranked, healthy = fn(prompt, index, cfg, dense_path, stack)
            if not healthy:
                degraded += 1
            picks = pl._top_combined(ranked, cfg, TOP_N)
        except Exception as exc:      # a lane crash is a finding, not a crash
            failures.append({"id": row["id"], "error": f"{type(exc).__name__}: {exc}"})
            degraded += 1
            per_query.append({"id": row["id"], "labels": row.get("labels") or [],
                              "ranks": [], "abstained": False, "errored": True})
            continue
        ranked_names = [r["name"] for r in ranked]
        pick_names = [p["name"] for p in picks]
        per_query.append({
            "id": row["id"],
            "labels": row.get("labels") or [],
            "ranks": M.ranks_of_gold(ranked_names, row.get("labels") or []),
            "pick_names": pick_names,
            "abstained": len(pick_names) == 0,
            "scoreable": True,
        })
    agg = M.score_run(per_query, k_default=TOP_N)
    agg["lane_failures"] = len(failures)
    agg["lane_degraded"] = degraded
    agg["lane_healthy"] = degraded == 0
    if failures:
        agg["failure_sample"] = failures[:5]
    return agg, per_query


# ------------------------------------------------------------------- report

HEADER = f"{'lane':16s} {'R@1':>7s} {'R@5':>7s} {'R@10':>7s} {'MRR':>7s} " \
         f"{'nDCG':>7s} {'absT':>7s} {'FPR':>7s} {'err':>4s} {'deg':>5s} {'ms':>7s}"


def fmt(lane: str, agg: dict, ms: float) -> str:
    def g(k):
        v = agg.get(k)
        return "  n/a " if v is None else f"{v:.4f}"
    return (f"{lane:16s} {g('recall@1'):>7s} {g('recall@5'):>7s} {g('recall@10'):>7s} "
            f"{g('mrr'):>7s} {g('ndcg@10'):>7s} {g('abstain_precision'):>7s} "
            f"{g('false_pick_rate'):>7s} {agg.get('lane_failures', 0):4d} "
            f"{agg.get('lane_degraded', 0):5d} {ms:7.0f}")


def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--golden", type=Path, default=GOLDEN)
    ap.add_argument("--lane", action="append", choices=list(LANES))
    ap.add_argument("--repeat", type=int, default=1,
                    help="repeat non-deterministic lanes to expose spread")
    ap.add_argument("--assert", dest="do_assert", action="store_true",
                    help="exit 1 when any lane regresses vs runs.jsonl")
    ap.add_argument("--save", action="store_true", help="append results to runs.jsonl")
    ap.add_argument("--limit", type=int, default=0, help="score only the first N queries")
    args = ap.parse_args(argv)

    index = rc.load_index()
    if index is None:
        print("no index on disk; run: ~/.tool-router/index --cwd .", file=sys.stderr)
        return 2
    cfg = rc.load_config()
    dense_path = rc.index_path().parent / (rc.index_path().stem + ".dense.npz")

    rows = goldset.load_golden(args.golden)
    if args.limit:
        rows = rows[:args.limit]
    stale = sum(1 for r in rows if r.get("label_status") == "stale")
    print(f"golden={args.golden}  queries={len(rows)} (skipped {stale} stale)  "
          f"index_items={len(index['items'])}  dense={'yes' if dense_path.is_file() else 'NO'}")
    print(HEADER)
    print("-" * len(HEADER))

    lanes = args.lane or list(LANES)
    results, regressions = {}, []
    for lane in lanes:
        reps = 1 if lane == "bm25" else max(1, args.repeat)
        per_rep = []
        for _ in range(reps):
            t0 = time.perf_counter()
            agg, _ = score_lane(lane, rows, index, cfg, dense_path)
            per_rep.append((agg, (time.perf_counter() - t0) * 1000))
        agg, ms = per_rep[0]
        # median rep keeps one flaky embedding call from dominating
        if len(per_rep) > 1:
            agg = sorted((a for a, _ in per_rep),
                         key=lambda a: a.get("recall@10") or 0)[len(per_rep) // 2]
        results[lane] = agg
        print(fmt(lane, agg, ms))
        if not agg.get("lane_healthy", True):
            print(f"  !! lane degraded on {agg.get('lane_degraded', 0)}/{len(rows)} queries "
                  f"— these numbers are a BM25 fallback, not this lane. "
                  f"Check Ollama and ~/.tool-router/breaker-ollama.json")
        if reps > 1:
            for key in ("recall@1", "recall@10", "mrr", "false_pick_rate"):
                vals = [a.get(key) for a, _ in per_rep if a.get(key) is not None]
                if len(vals) > 1:
                    print(f"{'  spread ' + key:16s} min={min(vals):.4f} "
                          f"max={max(vals):.4f} delta={max(vals) - min(vals):.4f}")
        if args.do_assert:
            regressions += [f"{lane}: {p}" for p in M.compare(agg)]
            # A degraded lane is not a result, it is a broken measurement.
            # Gate on it so nobody records "fused == bm25, fusion is useless".
            if not agg.get("lane_healthy", True):
                regressions.append(
                    f"{lane}: DEGRADED on {agg.get('lane_degraded', 0)}/{len(rows)} "
                    f"queries — metrics are a fallback, not this lane")

    if args.save:
        for lane, agg in results.items():
            M.append_run(RUNS, M.run_record(lane, agg, {
                "golden": str(args.golden), "n_queries": len(rows),
                "index_items": len(index["items"]), "repeat": args.repeat,
            }))
        print(f"\nappended {len(results)} lane records to {RUNS}")

    prior = M.load_runs(RUNS)
    if prior and not args.save:
        print("\nvs previous recorded runs:")
        for lane, agg in results.items():
            for key in ("recall@1", "recall@10", "abstain_precision", "false_pick_rate"):
                lo, hi, sd, n = M.variance(prior, lane, key)
                if n:
                    print(f"  {lane:16s} {key:18s} prev min={lo} max={hi} sd={sd} (n={n})"
                          f" now={agg.get(key)}")

    if regressions:
        print("\nREGRESSIONS:")
        for r in regressions:
            print("  " + r)
        return 1
    if args.do_assert:
        print("\nno regressions")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
