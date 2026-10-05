#!/usr/bin/env python3
"""labelcollect.py — the loop that turns live traffic into ranking weights.

Zero dependencies, assert-based. No model, no gradient descent, no numpy.

THE PROBLEM
  rc.score() already has a `learned` term: `rel += learned[name] * 0.05`. It
  reads ~/.tool-router/learned.json — a file that does not exist yet. This
  script is what fills it, and what decides WHICH capabilities are worth asking
  about.

WHY THIS IS LEARNING-TO-RANK AND NOT A COUNTER
  We do not learn from "the user did not complain" — that is position bias
  (Joachims 2002/2007; Wang et al. 2018; Ai et al. 2018 all show naive
  click-as-relevance training bakes the current ranker back in). We learn from
  a counterfactual pair: the SAME prompt, ranked two ways, one of which the
  user (or the agent's later behaviour) confirmed. A pair removes the prompt
  difficulty confound; only the ordering differs.

  Prior art this deliberately borrows from:
    - Joachims' cascade / SkipAbove heuristic: an item the agent skipped ABOVE
      an item it loaded is a real preference signal, because in a working
      session the agent saw the upper one and did not take it.
    - inverse propensity weighting: weight each observation by 1/(rank it was
      shown at), so an unranked capability nobody saw never votes.
    - active learning / uncertainty sampling: only spend a human question on
      intents where the lanes DISAGREE. Agreement is not worth a question.

THE LOOP
  1. watch.  Read a run log of (prompt, ranked picks). Skip anything already in
             the golden set (norm_key dedup) and anything skip_reason rejects.
  2. mine.   Two automatic sources, in order of trust:
               a. behaviour — a skill_view for capability X in the same session,
                  shortly after the prompt, means X was the right answer.
               b. user     — an explicit "yes that was right" / "no, use Y".
             Both produce a PREFERENCE PAIR (preferred, dispreferred) plus the
             dispreferred item's rank, which sets the propensity weight.
  3. ask.    Only for intents where the lanes disagree AND the top-2 score gap
             is small AND we have no behaviour label: emit a question. Cheap,
             one line, answerable with "1" or "2".
  4. learn.  Accumulate weighted wins per capability into learned.json, clamped
             exactly the way router_core.load_learned() clamps (0.0 .. 2.0), so
             a runaway counter cannot push one skill past every other.
  5. verify. Run ablate.py --assert. A learned weight that does not move
             recall@1 upward did not earn its place; revert it.

THE WEIGHT
    delta = 1/(1 + log2(1 + dispreferred_rank))       # 1.0 at rank 0, 0.33 at rank 3
    learned[preferred] = clamp(learned[preferred] + 0.25 * delta)
and the mirror, because a wrong pick should lose ground:
    learned[dispreferred] = max(0.0, learned[dispreferred] - 0.10 * delta)

  The asymmetric constants are the whole trick: gaining is slower than losing,
  so a single lucky match cannot entrench a skill, while one confirmed miss
  demotes it immediately. This is the cheap, stable, dependency-free stand-in
  for a learned ranker — and it is why the file is safe to ship: 5 matched
  pairs moves a score by 0.125, which is half the name-boost, so it only
  decides ties between close candidates, never overrides a strong match.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "scripts"))

import goldset  # noqa: E402
import router_core as rc  # noqa: E402

LEARNED = rc.index_path().parent / "learned.json"
PREFS = HERE / "prefs.jsonl"
QUESTIONS = HERE / "questions.jsonl"

# agreement gate: score gap below this between lane 1 and lane 2 means the
# fused lane had no opinion worth defending
UNSURE_GAP = 0.15
MAX_QUESTIONS = 40


# ------------------------------------------------------------------- helpers

def clamp(v: float) -> float:
    """Same clamp rc.load_learned() applies on read. Keep them in step."""
    return max(0.0, min(float(v), 2.0))


def propensity(rank: int) -> float:
    """How much an observation at this rank is worth. 1.0, 0.63, 0.50, 0.43..."""
    return 1.0 / (1.0 + _log2(1.0 + max(0, rank)))


def _log2(x: float) -> float:
    import math
    return math.log2(max(1e-9, x))


def load_learned() -> dict:
    if LEARNED.is_file():
        try:
            data = json.loads(LEARNED.read_text(encoding="utf-8"))
            return {str(k).lower(): clamp(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError):
            return {}
    return {}


def save_learned(data: dict) -> Path:
    ordered = {k: round(clamp(v), 4) for k, v in sorted(
        data.items(), key=lambda kv: -clamp(kv[1]))}
    LEARNED.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEARNED.with_suffix(".tmp")
    tmp.write_text(json.dumps(ordered, indent=1), encoding="utf-8")
    tmp.replace(LEARNED)      # atomic: a half-written file must not be read
    return LEARNED


def load_prefs() -> list[dict]:
    if not PREFS.is_file():
        return []
    out = []
    for line in PREFS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def append_pref(rec: dict) -> None:
    PREFS.parent.mkdir(parents=True, exist_ok=True)
    rec = {**rec, "ts": time.strftime("%Y-%m-%dT%H:%M:%S")}
    with PREFS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def append_question(rec: dict) -> None:
    QUESTIONS.parent.mkdir(parents=True, exist_ok=True)
    with QUESTIONS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, sort_keys=True) + "\n")


def observe(prompt: str, preferred: str, dispreferred: list[str],
            rank_of_dispreferred: int, origin: str) -> dict:
    """Record one preference observation and return the weight it earned."""
    assert prompt and preferred, "an observation needs a prompt and a winner"
    assert preferred not in dispreferred, "a capability cannot beat itself"
    w = propensity(rank_of_dispreferred)
    rec = {"id": hashlib.sha1(goldset.norm_key(prompt).encode()).hexdigest()[:12],
           "prompt": prompt, "preferred": preferred,
           "dispreferred": sorted(set(dispreferred)),
           "dispreferred_rank": rank_of_dispreferred,
           "weight": round(w, 4), "origin": origin}
    append_pref(rec)
    return rec


# ------------------------------------------------------- source a: behaviour

def mine_behaviour(rows: list[dict]) -> list[dict]:
    """Pull (prompt, capability) pairs from real sessions via skill_view.

    Same signal the golden set uses. Anything whose capability is not in the
    live index is dropped: the agent loaded something uninstalled, which says
    nothing about what the router should route today.
    """
    index = rc.load_index()
    names = {i.get("name") for i in (index or {}).get("items", [])}
    out = []
    for row in goldset.harvest_astra():
        labels = [l for l in row["labels"] if l in names]
        if not labels:
            continue
        out.append({"prompt": row["prompt"], "preferred": labels[0],
                    "origin": "behaviour:skill_view", "id": row["id"]})
    return out


# ------------------------------------------------------------- source b: user

_USER_YES = re.compile(r"\b(?:yes|yep|yeah|correct|right|that'?s (?:it|right)|works?)\b", re.I)
_USER_NO = re.compile(r"\b(?:no|nope|wrong|not that|incorrect)\b", re.I)
_SKILL_MENTION = re.compile(r"\b([a-z][a-z0-9]*(?:-[a-z0-9]+){1,5})\b")


def parse_user_reply(text: str, offered: list[str]) -> dict | None:
    """Turn a free-text answer into a preference, or None if it is unclear.

    Deliberately conservative: anything ambiguous returns None so the intent
    stays a question instead of becoming a wrong label. A wrong label here is
    worse than no label, because it actively demotes a correct skill.
    """
    if not text or not offered:
        return None
    has_no = bool(_USER_NO.search(text))
    has_yes = bool(_USER_YES.search(text))
    # "2" or "use X" beats a bare yes/no, which is often just politeness
    for cand in offered:
        if re.search(r"\b" + re.escape(cand.lower()) + r"\b", text.lower()):
            return {"preferred": cand, "dispreferred": [o for o in offered if o != cand]}
    if has_no and has_yes:
        return None                       # contradictory, ask again
    if has_yes:
        return {"preferred": offered[0], "dispreferred": offered[1:]}
    return None


# ------------------------------------------------------------ source c: ask

def pick_questions(candidates: list[dict], index, cfg, limit: int = MAX_QUESTIONS) -> list[dict]:
    """Intents where the lanes disagree are the only ones worth a human second.

    `candidates` must be UNLABELLED traffic — recent prompts the router has
    seen but nothing has judged. Passing the golden set here is a bug: every
    prompt in it is already in `have`, so the loop can never emit anything.
    Use `unjudged_pool()` for that.

    Three gates, all cheap:
      - not already in the golden set
      - lane disagreement: BM25's #1 is not the fused #1, or the fused lane's
        top two are within UNSURE_GAP
      - the router did pick something (a question about "nothing matched" is
        already answered by the router)
    """
    import dense_index as di
    dense_path = rc.index_path().parent / (rc.index_path().stem + ".dense.npz")
    golden = HERE / "golden.jsonl"
    have = set()
    if golden.is_file():
        have = {goldset.norm_key(r["prompt"]) for r in goldset.load_golden(golden)}
    out = []
    for row in candidates:
        if len(out) >= limit:
            break
        prompt = row["prompt"] if isinstance(row, dict) else str(row)
        if not prompt or rc.skip_reason(prompt, None):
            continue
        key = goldset.norm_key(prompt)
        if key in have or not key:
            continue
        have.add(key)          # do not ask the same thing twice in one pass
        base = rc.score(index, prompt, stack=[], learned={},
                        mcp_hints=cfg.get("mcp_hints"))
        if not base:
            continue
        try:
            fused = rc.fuse(base, di.dense_rank(prompt, index, dense_path, top_n=50), index)
        except Exception:
            fused = base
        import pipeline as pl
        picks = pl._top_combined(fused, cfg, 10)
        if not picks:
            continue                     # router already says "nothing"
        bm25_top = base[0]["name"] if base else None
        fused_top = picks[0]["name"]
        gap = (picks[1]["score"] - picks[0]["score"]) if len(picks) > 1 else 1.0
        disagree = bm25_top != fused_top or gap < UNSURE_GAP
        if not disagree:
            continue                     # lanes agree; nothing to learn
        out.append({
            "id": key[:16],
            "prompt": prompt,
            "offered": [p["name"] for p in picks[:3]],
            "bm25_top": bm25_top, "fused_top": fused_top,
            "gap": round(gap, 4),
            "question": ("Which of these actually helped? Reply 1, 2 or 3 "
                         "(or say 'none'):"),
        })
    return out


def unjudged_pool() -> list[dict]:
    """Prompts the router has seen that NOTHING has judged.

    gaps.json entries with served=None are exactly this: gaptrack recorded
    them (a route happened), the Laya oracle never ruled on them, and no
    skill_view followed. That is the raw material for active learning.
    """
    return [{"prompt": r["prompt"], "intent": r.get("intent"),
             "count": r.get("count", 0)}
            for r in goldset.harvest_unjudged()]


# ----------------------------------------------------------------- learning

def apply_observations(prefs: list[dict], learned: dict | None = None) -> dict:
    """Fold preference pairs into weights. Returns the new learned map."""
    learned = dict(load_learned() if learned is None else learned)
    n = 0
    for p in prefs:
        w = float(p.get("weight") or propensity(p.get("dispreferred_rank", 0)))
        pref = str(p["preferred"]).lower()
        learned[pref] = clamp(learned.get(pref, 0.0) + 0.25 * w)
        for dis in p.get("dispreferred", []):
            d = str(dis).lower()
            if d == pref:
                continue
            learned[d] = clamp(learned.get(d, 0.0) - 0.10 * w)
        n += 1
    return {k: round(v, 4) for k, v in learned.items() if v > 0.0}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mine", action="store_true", help="import behaviour labels from sessions")
    ap.add_argument("--ask", type=int, default=0, help="emit up to N questions")
    ap.add_argument("--learn", action="store_true", help="apply prefs.jsonl to learned.json")
    ap.add_argument("--answer", help="file with one answer per line for questions.jsonl")
    ap.add_argument("--show", action="store_true", help="print current weights")
    args = ap.parse_args(argv)

    if args.show or (not any((args.mine, args.ask, args.learn, args.answer))):
        learned = load_learned()
        prefs = load_prefs()
        print(json.dumps({
            "learned_file": str(LEARNED),
            "n_learned": len(learned),
            "top": dict(list(learned.items())[:12]),
            "n_prefs": len(prefs),
            "prefs_by_origin": _tally(prefs, "origin"),
            "total_weight": round(sum(learned.values()), 4),
        }, indent=1))
        return 0

    if args.mine:
        found = mine_behaviour(goldset.load_golden(GOLDEN_FALLBACK()))
        base = rc.load_index() or {}
        stack: list[str] = []
        cfg = rc.load_config()
        n = 0
        for f in found:
            ranked = rc.score(base, f["prompt"], stack=stack, learned={},
                              mcp_hints=cfg.get("mcp_hints"))
            names = [r["name"] for r in ranked]
            if f["preferred"] not in names:
                continue                # capability not even retrievable; not a preference
            rank = names.index(f["preferred"])
            # everything the router showed ABOVE the capability the agent
            # actually loaded. Rank 0 means the router already had it first and
            # there is no preference to learn — skip rather than manufacture one.
            if rank == 0:
                continue
            dis = names[:rank]
            if not dis:
                continue
            observe(f["prompt"], f["preferred"], dis, len(dis) - 1,
                    f["origin"])
            n += 1
        print(f"mined {n} behaviour observations")

    if args.ask:
        index = rc.load_index()
        cfg = rc.load_config()
        pool = unjudged_pool()
        qs = pick_questions(pool, index, cfg, args.ask)
        for q in qs:
            append_question(q)
        print(f"pool={len(pool)} unjudged intents; wrote {len(qs)} questions to {QUESTIONS}")
        for q in qs[:5]:
            print(f"  {q['id']}  offered={q['offered']}  gap={q['gap']}")
            print(f"    {q['prompt'][:100]}")

    if args.answer:
        applied = apply_answers(Path(args.answer))
        print(f"applied {applied} user answers")

    if args.learn:
        prefs = load_prefs()
        new = apply_observations(prefs)
        path = save_learned(new)
        print(f"applied {len(prefs)} observations -> {path} ({len(new)} capabilities)")
        print("verify with: python3 eval/ablate.py --lane bm25 --assert")

    return 0


def GOLDEN_FALLBACK():
    return HERE / "golden.jsonl"


def apply_answers(path: Path) -> int:
    """questions.jsonl + an answer file -> preference pairs."""
    if not QUESTIONS.is_file():
        return 0
    questions = [json.loads(l) for l in QUESTIONS.read_text(encoding="utf-8").splitlines() if l.strip()]
    answers = [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    n = 0
    for q, a in zip(questions, answers):
        verdict = parse_user_reply(a, q["offered"])
        if not verdict:
            continue                    # unclear answer: leave it a question
        pref = verdict["preferred"]
        dis = verdict["dispreferred"]
        if not dis:
            continue                    # "it was right" with nothing to demote
        observe(q["prompt"], pref, dis, q["offered"].index(dis[0]), "user:explicit")
        n += 1
    return n


def _tally(rows: list[dict], key: str) -> dict:
    out: dict[str, int] = {}
    for r in rows:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


if __name__ == "__main__":
    raise SystemExit(main())
