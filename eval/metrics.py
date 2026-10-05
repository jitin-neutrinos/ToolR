#!/usr/bin/env python3
"""metrics.py — retrieval metrics for the router, in plain terms.

Zero dependencies. Every formula is documented in words next to the code so a
future reader does not have to remember what nDCG means.

Why accuracy is wrong here
--------------------------
Plain accuracy would ask "did the top pick equal the one label?" Two things make
that meaningless for this router:

  1. MOST REAL PROMPTS HAVE NO CORRECT CAPABILITY. Trivial conversation, status
     questions, pure system noise. A router that always returns its single
     best match scores ~100% accuracy on that class while being useless. Worse,
     it can score *well* on a set by guessing on the negatives and nailing the
     positives, hiding a total failure to stay silent.

  2. THE LABEL IS USUALLY A SET, NOT ONE NAME. One prompt can legitimately need
     two capabilities. Scoring a hit only when pick #1 equals label #1 throws
     away the credit for a correct pick at position 3.

So positives are scored as RECALL (did a correct capability appear anywhere in
the top-k) and negatives are scored as SILENCE (did the router decline). The two
are never averaged into one number.

Definitions, plainly:

  recall@k        fraction of labelled prompts where at least one gold
                  capability appears in the ranked top-k. k=1 is "did the very
                  first suggestion work".
  MRR             mean of 1/rank of the BEST correct pick. Rewards putting the
                  right thing first, and is dominated by rank 1 — which is
                  exactly the behaviour we want.
  nDCG@10         ranks all correct picks, not just the best, and discounts by
                  log2(rank+1). Use it when a prompt may need several
                  capabilities and the ordering among them matters.
  abstain-precision
                  on prompts with NO gold capability: fraction where the router
                  returned an empty card. This is the false-positive guard.
                  A router that never abstains scores 0.0 here no matter how
                  good its recall is.
  FPR-at-card     fraction of negatives that produced any pick at all. The
                  complement of abstain-precision; report one, not both.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

LANES = ("bm25", "dense", "fused", "fused_rerank")


# ------------------------------------------------------------------ primitives

def ranks_of_gold(ranked_names: list[str], labels: list[str]) -> list[int]:
    """1-based ranks where a gold capability appears. [] if none do."""
    return [i + 1 for i, n in enumerate(ranked_names) if n in set(labels)]


def recall_at_k(ranked_names: list[str], labels: list[str], k: int) -> float:
    """1.0 if a gold capability is within the first k, else 0.0."""
    return 1.0 if any(r <= k for r in ranks_of_gold(ranked_names, labels)) else 0.0


def reciprocal_rank(ranked_names: list[str], labels: list[str]) -> float:
    """1/rank of the best gold hit; 0.0 if none in the whole list."""
    rs = ranks_of_gold(ranked_names, labels)
    return 1.0 / min(rs) if rs else 0.0


def log2(x: float) -> float:
    """log2 with a floor, so a rank-0 discount is exactly 1.0."""
    return math.log2(max(1e-9, x))


def dcg(gains: list[float], ranks: list[int] | None = None) -> float:
    """Discounted gain: a correct pick at rank r (1-based) counts gain/log2(r+1).

    Rank 1 = 1.0, rank 2 = 0.63, rank 10 = 0.29.

    Pass `ranks` to place each gain at its TRUE 1-based rank instead of packing
    the gains densely from position 1. Without it a gold hit at rank 2 is
    re-seated at rank 1 and scores a perfect 1.0 — the classic DCG bug, and it
    makes nDCG blind to ordering. Dense packing is still correct for the IDEAL
    list, where hits occupy the top slots by construction.
    """
    if ranks is None:
        return sum(g / log2(i + 2) for i, g in enumerate(gains))
    return sum(g / log2(r + 1) for g, r in zip(gains, ranks))


def ndcg_at_k(ranked_names: list[str], labels: list[str], k: int) -> float:
    """Ideal-vs-actual discounted gain over the top k.

    Gains are placed at their TRUE ranks, so a gold item at rank 2 scores
    1/log2(3) = 0.63, not 1.0. (Collecting only the gold hits into a list and
    running dcg over that list silently re-seats every hit at rank 1 and
    returns 1.0 for any single-gold query — the whole metric collapses to a
    constant and stops seeing order at all.)

    With one gold label, ideal puts it at rank 1, so nDCG reduces to
    1/log2(best_rank+1): the same shape as reciprocal rank but log-discounted.
    With several labels, it rewards getting all of them high.
    """
    return ndcg_from_ranks(ranks_of_gold(ranked_names, labels), len(labels), k)


def ndcg_from_ranks(ranks: list[int], n_labels: int, k: int) -> float:
    """nDCG from precomputed 1-based ranks of the gold hits.

    This is the form score_run uses: it takes ranks directly, so the position
    of each hit is preserved. ndcg_at_k() is the name-based convenience wrapper
    around it. Both share this one implementation on purpose — two copies of a
    ranking formula is how they drift apart.
    """
    hits = sorted(r for r in ranks if r <= k)
    if not hits or n_labels <= 0:
        return 0.0
    # actual: each hit at its true rank; ideal: same hits packed into the top
    # slots, which is the best any ranker could do
    actual = dcg([1.0] * len(hits), hits)
    idcg = dcg([1.0] * min(n_labels, k))
    return actual / idcg if idcg else 0.0


# ---------------------------------------------------------------- aggregation

def score_run(per_query: list[dict], k_default: int = 10) -> dict:
    """per_query: [{'labels':[...], 'ranks':[...], 'abstained':bool, ...}]

    Positives and negatives are reported in separate blocks on purpose — see
    the module docstring.
    """
    pos = [q for q in per_query if q["labels"]]
    neg = [q for q in per_query if not q["labels"] and q.get("scoreable", True)]

    def _r(k):
        return round(sum(1.0 for q in pos if any(r <= k for r in q["ranks"])) / len(pos), 4) if pos else None

    def _mrr():
        if not pos:
            return None
        return round(sum(1.0 / min(q["ranks"]) for q in pos if q["ranks"]) / len(pos), 4)

    def _ndcg(k):
        if not pos:
            return None
        tot = 0.0
        for q in pos:
            tot += ndcg_from_ranks(q["ranks"], len(q["labels"]), k)
        return round(tot / len(pos), 4)

    abstained = sum(1 for q in neg if q["abstained"])
    chatty = sum(1 for q in neg if not q["abstained"])

    return {
        "n_positive": len(pos),
        "n_negative": len(neg),
        "negative_fraction": round(len(neg) / len(per_query), 4) if per_query else 0.0,
        # positives
        "recall@1": _r(1),
        "recall@3": _r(3),
        "recall@5": _r(5),
        "recall@10": _r(k_default),
        "mrr": _mrr(),
        "ndcg@10": _ndcg(10),
        "unreachable@10": round(sum(1 for q in pos if not any(r <= 10 for r in q["ranks"])) / len(pos), 4) if pos else None,
        # negatives (the false-positive guard)
        "abstain_precision": round(abstained / len(neg), 4) if neg else None,
        "false_pick_rate": round(chatty / len(neg), 4) if neg else None,
        # the misleading one, reported only so it can be argued with
        "accuracy_naive": round(
            (sum(1 for q in pos if any(r == 1 for r in q["ranks"]))
             + abstained) / len(per_query), 4) if per_query else None,
    }


# ------------------------------------------------------------------ comparison

BASELINE = {
    # measured on this machine 2026-10-05 with the current index (759 items).
    # Re-measure after any corpus change; do not trust these as absolutes.
    "recall@1": 0.0, "recall@5": 0.0, "recall@10": 0.0,
    "mrr": 0.0, "ndcg@10": 0.0, "abstain_precision": 0.0,
}
GUARD = {
    # a lane change that improves recall but loses more than this on
    # false_pick_rate is a regression, not an improvement
    "false_pick_rate_max_regression": 0.02,
    "recall@1_min_regression": 0.0,
}


def compare(current: dict, baseline: dict = None) -> list[str]:
    """Return human-readable regressions. Empty list means no regression."""
    base = baseline or BASELINE
    problems = []
    for key in ("recall@1", "recall@5", "recall@10", "mrr", "ndcg@10"):
        now, before = current.get(key), base.get(key)
        if now is None or before is None:
            continue
        delta = now - before
        floor = -GUARD["recall@1_min_regression"]
        if delta < floor:
            problems.append(f"{key} dropped {delta:+.4f} ({before} -> {now})")
    now_fp = current.get("false_pick_rate")
    base_fp = base.get("false_pick_rate")
    if now_fp is not None and base_fp is not None:
        delta = now_fp - base_fp
        if delta > GUARD["false_pick_rate_max_regression"]:
            problems.append(f"false_pick_rate rose {delta:+.4f} ({base_fp} -> {now_fp})")
    return problems


# ----------------------------------------------------------------- run record

def run_record(lane: str, metrics: dict, extra: dict = None) -> dict:
    """One append-only JSONL line per lane per run. This is the regression log."""
    import datetime
    import hashlib
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "lane": lane,
        "metrics": metrics,
    }
    if extra:
        rec.update(extra)
    sig = hashlib.sha1(
        json.dumps({"lane": lane, "m": metrics}, sort_keys=True).encode()
    ).hexdigest()[:12]
    rec["sig"] = sig
    return rec


def append_run(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def load_runs(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def variance(runs: list[dict], lane: str, key: str) -> tuple[float, float, float, int]:
    """(min, max, stdev, n) for one metric on one lane across recorded runs.

    If the spread is wide, a "regression" is noise. Compare stdev against the
    change you care about before believing it.
    """
    vals = [r["metrics"].get(key) for r in runs
            if r.get("lane") == lane and r["metrics"].get(key) is not None]
    if not vals:
        return (None, None, None, 0)
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return (round(min(vals), 4), round(max(vals), 4), round(var ** 0.5, 4), n)
