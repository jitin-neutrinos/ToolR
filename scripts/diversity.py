#!/usr/bin/env python3
"""diversity.py — Layer 2: query-residual diversity for card selection.

Port of the DSR insight (arXiv:2609.05824) to a no-ML router: two picks that
cover the SAME part of the task waste a slot; two picks covering DIFFERENT
parts both earn a slot even if their scores are similar.

Zero-ML implementation of the query-residual idea: represent each candidate
by its token vector, subtract the query-aligned component (tokens the candidate
shares with the QUERY), then measure pairwise overlap on what remains. High
residual overlap = redundant (one covers the other's ground). Low residual
overlap = complementary (they're both on the card because of the query, but
they do different work).

    residual(i) = tokens(i) - tokens(i ∩ query)
    redundancy(i, j) = |residual(i) ∩ residual(j)| / |residual(i) ∪ residual(j)|

Greedy selection: walk the ranked list; keep a candidate unless its residual
Jaccard against an ALREADY-KEPT pick >= threshold (config
diversity.max_jaccard, default 0.85). The higher-scored pick always wins the
comparison. Pure stdlib, microseconds per pair.
"""
from __future__ import annotations


def residual_tokens(row: dict, query_tokens: set) -> frozenset:
    """Candidate's tokens minus what it shares with the query.

    Rows carry `hits` (the matched terms). If `tokens` is present on the row
    (score() strips it — so fall back to hits), intersect with the query set.
    """
    toks = row.get("tokens")
    if toks:
        return frozenset(t for t in toks if t not in query_tokens)
    hits = set(row.get("hits") or [])
    return frozenset(t for t in hits if t not in query_tokens)


def jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def diversify(ranked_rows: list[dict], query_tokens, max_jaccard: float = 0.85,
              keep_floor: int = 2) -> list[dict]:
    """Greedy diversity pass over a scored, ordered pick list.

    keep_floor: never drop below this many picks even if all are similar —
    a unanimous corpus is information too, and the card prints scores.
    Rows must be in score order (they are, from fuse/score). Returns the
    kept rows in the same order.
    """
    kept: list[dict] = []
    kept_res: list[frozenset] = []
    for row in ranked_rows:
        r = residual_tokens(row, query_tokens)
        redundant = any(jaccard(r, kr) >= max_jaccard for kr in kept_res)
        if redundant and len(ranked_rows) - 0 > keep_floor:
            row.setdefault("redundant_with", True)
            continue
        kept.append(row)
        kept_res.append(r)
    return kept if len(kept) >= keep_floor else ranked_rows[:keep_floor]
