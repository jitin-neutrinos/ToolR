# Why `--top 20` returned portfolio-revamp (2026-10-05)

## The short version

The user asked whether they were confusing something. **They were not.** `--top 20`
returned exactly 20 picks. The count was correct; the *ranking* was wrong, and the
ranking was broken by two silent bugs that made the entire alias layer a no-op in
production.

## Symptom

`~/.tool-router/route --top 20 "enhance enrich revamp overhaul the chat composer"`

| rank | before | after |
|---|---|---|
| 1 | `jitinnair-portfolio-revamp` — matched `revamp,overhaul` | `astra-webui-performance` — matched `chat` |
| 2 | `agent-transcript-datasets` — matched `chat` | `agent-transcript-datasets` — matched `chat` |
| 3 | `astra-webui-performance` — matched `chat` | `emil-design-eng` — matched `chat,composer` |
| 4 | `astra-canvas` — matched `chat` | `design-taste-frontend` — matched `chat,composer` |
| — | `impeccable` **NOT RANKED** | `impeccable` ranked, `chat,composer` |
| — | `frontend-design` **NOT RANKED** | `frontend-design` ranked, `chat,composer` |
| — | `design-taste-frontend` **NOT RANKED** | flagged `adjective-only` where matched |

## Bug A — `load_index()` discarded the whole alias layer

`save_index()` strips `tokens` from every item to keep `index.json` small (370 KB
instead of several MB). `load_index()` then recomputed them:

```python
it.setdefault("tokens", tokenize(" ".join((it["name"], it.get("desc", ""), it.get("extra", "")))))
```

**name + desc + extra only.** The alias terms were never part of that expression, so
every alias was dropped on load. A fresh in-process `build_index()` ranked
`emil-design-eng` #2 for "chat composer"; the saved index had no token `composer`
at all and the skill did not appear in the results *anywhere*.

This made the alias layer I built earlier today a complete no-op in production —
it only ever worked in tests and in the one code path that builds fresh.

**Fix:** `load_index()` re-applies `aliases.alias_terms_for(name)`. The `aliases`
field itself *is* persisted (`save_index` keeps every key except `tokens`), so this
only re-tokenizes; it is the same terms `build_index` bakes in.

## Bug B — the dense lane never learned the aliases either

`dense_index.build_dense()` embedded:

```python
texts.append(f"{kind}: {name}. {desc}")     # no alias terms
```

So the semantic lane was working from a *different corpus* than BM25. For the
chat-composer query it ranked `revise-claude-md` and a plugin named `access` at
dense ranks 2–3 — neither has anything to do with the request — while the design
skills sat at dense rank 17–18.

RRF is rank-only, and with k=60 the head is nearly flat: rank 0 is `0.01667`,
rank 17 is `0.01300`. A 22% gap that a confidently-wrong top-2 wins easily. The
BM25-exact matches were buried by the fusion.

**Fix:** `build_dense()` embeds the alias terms too, and all 766 vectors were
rebuilt from scratch (the content hash did not change, so the incremental builder
would have skipped them).

## Also fixed

- Aliases extended with the vocabulary people actually type for UI work
  (`composer`, `input`, `bubble`, `thread`, `sidebar`, `widget`, `panel`,
  `dialog`, `toolbar`, …) and for the Astra and Jitin Nair project skills, so a
  generic ask no longer lands on one specific project.
- Adjective-only matches (`revamp`, `overhaul`, `enhance`, `enrich`, `polish`,
  `premium`, …) are now **labelled in the card**, so a weak pick is visibly weak
  instead of silently occupying slot 1.
- `aliases.py` self-audit grew 64 → 68 keys, zero phantom keys.

## Tried and deliberately reverted

Reordering `fuse()` so a lexical match cannot be buried by a noisy dense arm.
It fixed the order, and then broke the opposite case — `revamp the portfolio page
on jitinnair.com` lost its own skill — and the score printed in the card stopped
matching the order the card printed. Correcting the *order* needs a score-aware
convex fusion measured against the golden set, not a drive-by edit.

That work is `RESEARCH-retrieval-quality-2026-10-05.md` **R1**: Bruch, Gai &
Ingber, *An Analysis of Fusion Functions for Hybrid Retrieval*, ACM TOIS 42(1)
(arXiv:2210.11934) — a convex combination of normalised scores beats RRF(60,60) on
nDCG across all nine benchmarks, in-domain and zero-shot, and α converges from
under 5% of the training data. The eval harness in `eval/` exists to prove it.

## Known remaining defect

The project skill still outranks the design skills on raw score for the generic
query: `jitinnair-portfolio-revamp` 0.372 (adjectives only) versus
`emil-design-eng` 0.253 (on `chat`+`composer`). `chat` carries more IDF than
`composer`, so the arithmetic favours the wrong answer.

Left visible rather than papered over. The `adjective-only` label in the card is
the honest signal: the reader can see that pick matched only adjectives.
