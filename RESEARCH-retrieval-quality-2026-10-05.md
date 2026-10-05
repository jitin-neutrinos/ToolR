# Retrieval-quality research for the tool-router: query-side + ranking-algorithm improvements

Date: 2026-10-05. Scope: the 759-item hybrid capability router at `~/Work/tool-router`
(`scripts/router_core.py`, `scripts/pipeline.py`). Every claim below is either (a) read out of the
code on this machine, (b) measured on this machine, or (c) traced to a URL in the Sources section.
Unverified items are labelled as such.

## 0. Facts established about this corpus and this pipeline (measured, not assumed)

| Fact | Value | How |
|---|---|---|
| Items | 759 (552 skill, 62 plugin-skill, 53 mcp, 40 agent, 33 command, 14 fleet-tool, 5 fleet-plugin) | `router_core.load_index()` |
| Item token length | median **25**, p75 45, p90 65, max 154, mean 33.3 | computed from `it["tokens"]` |
| Description length | median **199 chars**, p90 541, max 1428 | `it["desc"]` |
| Vocabulary | 5,026 terms; **2,805 (56%) occur in exactly one item** | df histogram |
| Most frequent terms | `skill` (162), `code` (151), `user` (135), `review` (125), `pattern` (117) | df histogram |
| BM25 params in code | `k1=1.2, b=0.6`, hardcoded, no config key | `router_core.py:584` |
| IDF | `log(1 + (n-d+0.5)/(d+0.5))`, then multiplied by `GENERIC_DAMP` if the term is in `GENERIC` (56 terms) | `router_core.py:591-594` |
| Normalisation | BM25 score divided by `q_idf` (sum of query IDFs) to make it corpus-size independent | `router_core.py:599,633` |
| Fusion | RRF k=60, **used to ORDER only**; `select()` filters on a different field (`score`) | `router_core.py:871-909`, `921-932` |
| Dense bonus | `0.35 * (1 - dense_rank/50)`, added to the BM25-relative score | `router_core.py:887-891` |
| Selection | `min_score=0.28`, `tail_ratio=0.55`, top-5 skills / top-3 others | live `~/.tool-router/config.json` |
| Dense index text | `f"{kind}: {name}. {desc}"` — **name+desc only, no body** | `dense_index.py` |
| Machine | 28 cores, 62 GB RAM, torch 2.14, sentence-transformers 5.7 | `nproc`, `free`, pip |

### Measured latencies on this box (warm, 8 torch threads, 762 docs, median 218 chars)

| Operation | Measured |
|---|---|
| `bge-small-en-v1.5` query encode | **5.9 ms/query** |
| `bge-small-en-v1.5` full reindex of 762 docs | **1.3 s** |
| `ms-marco-MiniLM-L-6-v2` cross-encoder, top-20 rerank | **35 ms/query** (140.8 ms/pair) |
| `bge-reranker-base` cross-encoder, top-20 rerank | **69 ms/query** (275.9 ms/pair) |
| mean cosine of dense top-1 for 5 probe queries | 0.681 |

### The structural finding that outranks everything else

`fuse()` sorts by the RRF key, but `select()` and `_top_combined()` cut on `score`
(= BM25-relative + dense_bonus). Two different orderings, one of which is discarded.

With k=60 and two arms, the RRF key is nearly flat at the head:

```
rank  0 -> 1/(60+0)  = 0.01667  (50.8% of the 0.03279 two-arm maximum)
rank  1 -> 1/(60+1)  = 0.01639  (50.0%)
rank  2 -> 1/(60+2)  = 0.01613  (49.2%)
rank  5 -> 1/(60+5)  = 0.01538  (46.9%)
rank 10 -> 1/(60+10) = 0.01429  (43.6%)
rank 20 -> 1/(60+20) = 0.01250  (38.1%)
rank 50 -> 1/(60+50) = 0.00909  (27.7%)
```

Ceding the top rank costs ~1.7% of the fused key; landing at rank 20 costs ~23%. So the key
rewards *agreement* strongly and *rank position* weakly — correct in general, but it means the RRF
ordering carries almost no information about which of two agreeing items is better. Measured over 6
probe queries: RRF order and score order differed in the top-5 for **1/6**. Meanwhile `score` is
driven almost entirely by BM25, because the dense bonus (max 0.35) is small next to relative BM25
scores that run 0.5–2.5. Net effect: **fusion reorders almost nothing and the dense lane
influences the cut mostly through a bounded additive bonus.**

## 1. Ranked recommendations

Ordered by (gain x confidence) / effort. "Validatable here" = can it be measured on this machine today.

### R1. Make the fused key authoritative: select on one ordering, and replace RRF with a tuned convex combination
**Gain: high. Effort: S (a day, mostly test data). Cost: zero. Validatable: yes — this is the top blocker.**

Bruch, Gai & Ingber (ACM TOIS 42(1), arXiv:2210.11934) is the decisive source and it reverses the
usual folklore:
- They run BM25 × all-MiniLM-L6-v2 across 9 datasets and report that a convex combination of
  normalised scores (TM2C2) **beats RRF(60,60) on NDCG on all nine**, in-domain *and* zero-shot.
- RRF is "sensitive to its parameters"; "NDCG swings wildly" across the (k_lex, k_sem) grid, and
  tuned RRF "does not generalize well to out-of-domain datasets" (their Table 3).
- Their punchline: RRF as usually framed is *parametric* — one constant per arm, i.e. m parameters
  versus m−1 for a convex combination. The fusion chosen for being parameter-free has one more
  parameter than the one it is chosen over.
- **Sample efficiency: "with less than 5% of the training data, which is often a small set of
  queries, TM2C2's α converges, regardless of the magnitude of domain shift."** RRF is "relatively
  less sample-efficient and converges to a relatively less effective retrieval system." Adding a
  third parameter to weighted RRF ("rrf-CC") made "no significant impact".

What to build, concretely:
1. Compute both arms' **scores** over the union set, not just ranks. You already have BM25 `raw`;
   the dense side needs the cosine, which `dense_index.dense_rank` currently discards.
2. Normalise each arm per query. Use **theoretical min-max (TM2C2)**: BM25's floor is 0, cosine's
   floor is -1 (or its observed minimum). Bruch's Theorem 4.5 shows min-max vs z-score are
   rank-equivalent under retuning, and that using the infimum is *more* robust across domains
   because it has one fewer data-dependent statistic. Note the alternative: percentile/PIT
   normalisation was "directionally more robust than min-max" (1W/6L, p=0.125) in a different
   setting (graph-vector multi-hop) — weaker evidence, but cheap to try.
3. Fuse as `alpha * cos_n + (1-alpha) * bm25_n`, sweep alpha ∈ {0.3, 0.4, 0.5, 0.6, 0.7, 0.8} on a
   hand-labelled set. Bruch: "the range α ∈ [0.6, 0.8] consistently lead[s] to improvements" in their
   setup; the optimum is corpus-specific so measure, don't inherit.
4. **Then make `select()` cut on the same number.** Today it cuts on `score`; after this change it
   must cut on the fused value, or step 3 is decorative.

If you want an intermediate step that keeps RRF: note Elasticsearch's RRF retriever natively
supports per-retriever weights and `rank_constant`, so weighted RRF is a config change, not a
rewrite. Bruch's evidence still favours going to the convex combination.

### R2. Build a 60–120 query eval set first; it is the prerequisite for every other item
**Gain: enables R1, R4, R5, R7. Effort: M (one focused session, or a day of harness runs). Cost: zero. Validatable: trivially — it is the validation.**

Bruch's α-converges-under-5%-of-training finding means tens of labels is the correct budget, not a
wish. Practical shape: write 80–120 real requests you actually send this router (one per skill
cluster is fine), and for each record the 1–3 item names that would have been right. Then compute
**MRR@5 and nDCG@10, plus the metric you care about operationally: "was the correct item in the
card at all" (recall@5)** — that last one is what a routing card actually needs. Judge pairs
with a local model, not an API: `bge-reranker-base` or the Laya `rank` tool can order a shortlist,
and disagreements are cheap to adjudicate by hand.

Do this **before** touching fusion. Every number below is only meaningful against a fixed baseline,
and `scripts/selftest.py` (415 lines) exists but does not measure relevance.

### R3. Field-weighted BM25 with a name field, and k1/b swept rather than hardcoded
**Gain: medium-high (this is the documented single biggest BM25-side lever). Effort: S. Cost: zero. Validatable: yes.**

The corpus has a strong field structure the current scorer flattens: a hit in `name_tokens` gets a
flat `*=2.5` multiplier applied to the BM25 term contribution (`router_core.py:617-618`), which is
not the same as a field boost (field boosts apply after length normalisation and interact with k1).
Descriptions are also very short (median 199 chars) while names are single tokens, so per-field
length normalisation matters more here than in a typical document corpus.

BM25Q is the cheap, well-evidenced variant: weighting the query itself with
`k1*(1-b+b*|Q|/avgq)` and introducing an `IDF(t)²` term "significantly increases the weight of rare
terms". In the BRIGHT baselines, reproducing the lexical baseline and identifying BM25Q as the
under-documented detail "significantly" moved Recall@100, and both RRF and NAF fusion then added
further Recall@100 "without negatively impacting NDCG@10". NAF came out as the best-average,
parameter-light combiner in that study.

On k1/b: I found **no** strong recent source saying k1/b tuning matters much on a corpus this size,
and the Anserini default `k1=0.9, b=0.4` differs from the `1.2/0.6` used here. So: sweep
k1 ∈ {0.9, 1.2, 1.5} × b ∈ {0.4, 0.6, 0.75} on the R2 set. Nine runs, minutes of compute. Do not
assume the current values are right — they are currently hardcoded and untested.
*Caveat, stated plainly: "tuning k1/b yields substantive improvements" appears in vendor/aggregator
material, not in a paper I retrieved. Treat the gain as unverified until your own sweep measures it.*

### R4. A local cross-encoder rerank over the fused top-20
**Gain: high, the largest single quality lever available offline. Effort: S. Cost: 35–69 ms measured. Validatable: yes — I already ran it.**

Measured on this box, top-20, warm:

| Model | Size (safetensors, HF API) | Licence | CPU latency here |
|---|---|---|---|
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | 91 MB | Apache-2.0 | **35 ms/query** |
| `cross-encoder/ms-marco-MiniLM-L-12-v2` | 134 MB | Apache-2.0 | not measured; ~2x the L-6 |
| `BAAI/bge-reranker-base` | 1,112 MB | MIT | **69 ms/query** |
| `BAAI/bge-reranker-v2-m3` | 2,271 MB | Apache-2.0 | not measured; the 2026 quality default but 568M params |

Quality evidence from a SemEval-2026 Task 8 system paper comparing rerankers at k=50 (nDCG@5):
`bge-reranker-v2-m3` 0.416, IBM Granite R2 0.391, `ms-marco-MiniLM-L-12-v2` 0.375,
`bge-reranker-base` 0.307 — note **`bge-reranker-base` is the *worst* of the four**, so the usual
"bge-reranker-base is the fast one" instinct is not supported; MiniLM-L-12 beats it. The same
paper's pool-size sweep is directly relevant: three of four rerankers peak at k=30, the strongest
at k=50, and all degrade by k=500. Your pipeline reranks top-5 (Laya) — going to top-20 is inside
the good regime, and beyond ~50 is where it starts hurting.

Counter-evidence worth taking seriously: on BEIR SciFact an off-the-shelf MS MARCO cross-encoder
applied to a fused top-50 *fell to* 0.694 nDCG@10 from 0.707 for equal-weight RRF. A reranker that
was never trained for your text can hurt. This is why R2 comes first.

Practical note: this replaces the currently-broken Laya rerank stage (it returns 401 — `curl
http://127.0.0.1:8015/health` gives `{"error":"unauthorized"}`). 35–69 ms is a good trade for a
routing decision, and it removes a network hop and a credential from the hot path.

### R5. Swap nomic-embed-text for a purpose-built asymmetric retriever
**Gain: medium-high. Effort: S. Cost: 5.9 ms/query, 1.3 s full reindex (measured on bge-small). Validatable: yes.**

`nomic-embed-text` is a 274 MB, 768-dim, Apache-2.0 general-purpose embedder. It is not trained on
query→document pairs. Asymmetric retrievers are, and that asymmetry is precisely what short
capability requests need. From the HyDE paper's framing: a question and its answer "share one
content word", and models trained on generic text similarity place such texts near each other only
by reading alike.

Mean MTEB **Retrieval nDCG@10** I computed from the HF API (mean over the 27 retrieval tasks each
model reports — my aggregation, not a published macro number, and not comparable to the
multilingual MMTEB figures these model cards quote):

| Model | Licence | MTEB retrieval nDCG@10 (mean of 27 tasks) | safetensors |
|---|---|---|---|
| `Snowflake/snowflake-arctic-embed-m` | Apache-2.0 | **49.97** | 436 MB |
| `BAAI/bge-base-en-v1.5` | MIT | 48.41 | 438 MB |
| `BAAI/bge-small-en-v1.5` | MIT | 46.07 | 134 MB |
| `thenlper/gte-small` | MIT | 45.25 | 67 MB |
| `intfloat/e5-base-v2` | MIT | 45.07 | 438 MB |
| `intfloat/multilingual-e5-base` | MIT | 44.27 | 438 MB |
| `jinaai/jina-embeddings-v2-base-en` | Apache-2.0 | 44.08 | 275 MB |
| `intfloat/e5-small-v2` | MIT | 43.72 | — |

Recommendation: **`bge-small-en-v1.5`** for the router. 134 MB, 384-dim, MIT, 5.9 ms/query and
1.3 s to reindex all 762 docs on this box, and it is already partly in the HF cache
(`models--BAAI--bge-small-en` is present). 6× smaller than nomic and measurably sharper on
retrieval. `bge-base-en-v1.5` if you have headroom. `gte-small` at 67 MB is the size-floor option.
TREC 2025 corroborates the ordering: a team using BGE-small and Qwen3-Embedding-0.6B found
"e5-small ... performed worse than BGE-small and was not adopted".

Two things to get right: bge-small expects the query instruction
`"Represent this sentence for searching relevant passages: "` prepended to the *query only* (never
the document), and e5 models require the literal `query:` / `passage:` prefixes — get these wrong
and quality drops for reasons that look like a model problem. `Qwen3-Embedding-0.6B` (Apache-2.0,
1024-dim, MRL to 32-dim, 64.33 MMTEB multilingual) is the strongest option if you can afford a
0.6B model; there is an Ollama build (`dengcao/Qwen3-Embedding-0.6B:F16`, 1.2 GB) and the TREC
paper reports it working in this exact role. I did not measure its CPU latency here.

### R6. Query-side: skip HyDE, do cheap multi-query and a real spelling normaliser
**Gain: medium. Effort: S. Cost: one extra local LLM call, or zero. Validatable: yes.**

The brief asks whether these are worth the complexity for 759 docs. My read, with reasons:

- **HyDE: not worth it.** The paper's own claim is narrow — it beats *unsupervised* Contriever
  zero-shot; it does not claim to beat a fine-tuned in-domain retriever, and that is the most common
  reason teams try it and see nothing. Once you run bge-small (R5), the asymmetry HyDE exists to
  fix is already fixed. Worse for *this* corpus specifically: HyDE's documented failure mode is
  "a confident wrong entity" — the LLM invents plausible tool and product names, and "those invented
  names are rare, high-information tokens, and they dominate the embedding." Your corpus is a list
  of real, named capabilities where the name IS the signal. A hallucinated `keys.rotate` would pull
  the search toward a capability that does not exist. You already run a gemini-flash rewrite stage;
  do not add a second generative hop that can fabricate identifiers.
- **Query decomposition: only for genuinely multi-intent requests.** TREC 2025's MIT LL system
  notes the 2025 queries were "long and complex, requiring many hops with synthesis across many
  documents" where 2024's "were simple". For single-capability routing requests decomposition is
  pure latency and noise.
- **Multi-query / multi-vector averaging: worth it, cheaply.** The one HyDE mechanism that survives
  the criticism is *sampling several hypotheticals and averaging their embeddings with the real
  query vector*, which is robust because one bad generation cannot dominate. You can get the same
  effect with no LLM at all: embed the raw prompt **plus** its `route`-style keyword extraction
  (`rc.scaffold` already computes one) and average the two normalised vectors. That is RRF over an
  extra arm, for free.
- **Rocchio / RM3 PRF: skip.** It needs feedback documents; with no labels you are doing PRF, and
  PRF's own literature shows the failure mode is query drift. A 2026 systematic study of PRF with
  LLMs found model-generated feedback helps BM25 by roughly a point on average via Rocchio, i.e.
  real but small, and its latency depends on candidate count or document length. On a 759-doc
  corpus where BM25 already returns the right item in the top 5 for most exact requests, there is
  little drift to fix.
- **SPLADE / learned sparse: not worth it at this size.** SPLADE's selling point is matching dense
  effectiveness at BM25-like FLOPS (MRR@10 0.322 vs ANCE 0.330 on MS MARCO dev). It needs a BERT
  encoder pass per document and per query, an inverted index over the full ~30k vocab, and it buys
  you learned expansion — which is precisely what the dense arm already gives you. Running three
  lanes (BM25 + dense + SPLADE) for 759 docs is over-engineering.
- **What *is* worth fixing: the stemmer.** `norm()` strips suffixes in the order
  `ing, ers, ed, es, s` with `len(w) > 4` (`router_core.py:432-436`). This is crude and lossy in ways
  that hurt a corpus where tokens are already short: "reviews" → "review" is fine, but "es" before
  "s" never fires on "boxes"→"box" correctly ordered, and more importantly the 5,026-term vocab
  with **2,805 singleton terms (56%)** means over half your vocabulary carries almost no IDF signal
  and only exact-match rescues it. A proper lemmatiser, or at minimum a curated alias map (the
  existing `ALIASES` table is a good pattern — extend it with the terms your queries actually miss),
  is a much better use of effort than any of the above.

### R7. Learn the weights you *can* learn from almost no data: prior-use frequency only
**Gain: small-to-medium, and it is the only part of R7 worth doing here. Effort: S. Cost: zero. Validatable: partially.**

Honest assessment: with tens of labels you should **not** attempt LambdaMART or Unbiased LambdaMART.
Unbiased LambdaMART needs position-bias estimation from randomised or modelled click data; with a
card of 5 items and no clicks you cannot identify the bias, and the machinery would be
unfalsifiable on your own data. Bruch's result is the actionable one: **a single α needs a handful of
labels; a learned ranker needs far more.**

What is worth doing:
- Keep the existing `learned` prior (`rel += learned[nm] * 0.05`, `router_core.py:648`) but **make
  it decay and normalise** — an unbounded counter means one popular skill slowly dominates forever.
- Extend the signal to *rejection*, not just use: `route.py` already computes `bool(picks)` per
  prompt via `_save_state`, and there is a `gaps.json` and a `source.py` that logs what was *not*
  found. A skill that was never loaded after being shown on a card is negative evidence.
- Use the **pairwise** signal cheaply. For each card, the item the user actually loaded is a
  preference over the items above it. Bradley–Terry over a few hundred such comparisons gives you a
  per-skill prior with 2 free parameters and no gradient boosting. Cite for the format:
  `SONS OF LAMBDA` (Microsoft Research TR, RankNet→LambdaRank→LambdaMART) if you want the lineage;
  cite EMNLP 2025 Findings 1259 for why *pairwise* beats pointwise when each judgment grounds the
  other. Either way, the honest claim is "a prior", not "a learned ranker".
- If you want LLM-judged labels instead of clicks: pointwise 0/1 + a one-sentence rationale, judged
  by a local model, is the pattern in arXiv:2606.22961. Just budget for judge bias — the ICLR 2025
  and EMNLP 2025 judge papers are blunt that LLM-judge setups are hackable, non-reproducible and
  biased by presentation order.

### R8. Fix the rerank-stage order of operations (do this while you are in the file)
**Gain: medium. Effort: XS. Cost: zero. Validatable: yes.**

`_route_once` runs BM25 → dense → fuse → **Laya rerank on the fused order**. If you adopt R4's
cross-encoder, rerank *after* fusion (correct) but consider whether Laya's "chance-based accept
gate" should gate on the fused order or on the cross-encoder's. Also: `fuse()` receives
`top_n=50` and truncates the BM25 arm at 50 while the dense arm is 50, so a strong BM25 item at
rank 51 is invisible to fusion but visible to `select()`. Align the two cut-offs.

## 2. Explicitly not recommended (with reasons)

- **Tuning RRF's k on this corpus.** Bruch: tuned RRF does not transfer out-of-domain, and here the
  corpus *is* the domain, but the leverage is small — k=60 puts ranks 0–5 within 1.7% of each other.
  If you keep RRF, sweep k once (10/20/60) and stop.
- **CombSUM / CombMNZ / MaxP as a starting point.** They assume commensurable scores, which is
  exactly the condition R1 establishes by construction. Once you have normalised scores, CombSUM
  *is* the convex combination with α=0.5; CombMNZ multiplies by hit-count, which is already what
  RRF's reciprocal terms reward. No separate win to chase. (Fox & Shaw's CombMNZ edged RRF by a
  small, non-significant margin in Cormack's own TREC evaluation: 0.6107 vs 0.6051, p≈0.2.)
- **RRF-Lambda as a named technique.** I could not retrieve a primary source for it. Searches for
  the term returned only vendor docs for Elasticsearch's `rrf` retriever (which exposes
  `rank_constant` and per-retriever weights) and unrelated code. If you want per-arm weights, that
  is weighted RRF / the `rrf-CC` variant Bruch tested — and he found the third parameter made no
  significant difference. **Treat "RRF-Lambda" as unverified.**
- **ColBERT / late interaction.** 30 KB/doc storage and PLAID/IVF machinery for a 759-doc corpus
  where brute-force cosine over a dense matrix takes single-digit milliseconds.

## 3. Suggested order of work

1. R2 — 80–120 labelled queries, a metric script. Nothing else is measurable without it.
2. R1 — score-based convex fusion, and make `select()` cut on the fused value. Highest gain, zero cost.
3. R5 — bge-small-en-v1.5, reindex (1.3 s), re-measure.
4. R4 — MiniLM-L-6-v2 cross-encoder over fused top-20 (35 ms). Retires the 401 Laya hop.
5. R3 — field-weighted BM25 + k1/b sweep against the R2 baseline.
6. R6 — query-prefix + query-type-as-second-query multi-query, and a real normaliser.
7. R7 — decayed, rejection-aware usage prior.

## 4. Sources actually retrieved

Primary research
- Cormack, Clarke & Büttcher (SIGIR 2009), *Reciprocal Rank Fusion outperforms Condorcet and individual Rank Learning Methods* — RRF definition, k=60 chosen in a pilot and not altered, RRF beats Condorcet (p≈0.004), CombMNZ edge non-significant (p≈0.2). https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf
- Bruch, Gai & Ingber, *An Analysis of Fusion Functions for Hybrid Retrieval*, ACM TOIS 42(1), arXiv:2210.11934 — the central source for R1. https://arxiv.org/html/2210.11934 and https://arxiv.org/abs/2210.11934
- Gao et al., *Precise Zero-Shot Dense Retrieval without Relevance Labels* (HyDE), arXiv:2212.10496 — scope of the claim, DL19 nDCG@10 61.3 vs Contriever 44.5 vs ContrieverFT 62.1. https://arxiv.org/pdf/2212.10496
- Formal, Piwowarski & Clinchant, *SPLADE*, arXiv:2107.05720 — MRR@10 0.322 vs ANCE 0.330, FLOPS-regularised sparsity. https://arxiv.org/pdf/2107.05720.pdf
- Jedidi & Lin, *Revisiting Feedback Models for HyDE*, arXiv:2511.19349 — Rocchio/RM3 beat naive concatenation for LLM-based PRF. https://arxiv.org/abs/2511.19349
- *A Systematic Study of Pseudo-Relevance Feedback with LLMs*, arXiv:2603.11008 — Rocchio ≈ +1 point over RM3 for model-generated feedback; corpus-feedback latency. https://arxiv.org/pdf/2603.11008
- Hu, Wang, Peng & Li, *Unbiased LambdaMART*, CIKM 2019 — position-bias estimation is the blocker for click-based LTR. https://dl.acm.org/doi/fullHtml/10.1145/3308558.3313447
- Burges et al., *From RankNet to LambdaMART*, MSR-TR-2010-82 — LTR lineage. https://www.microsoft.com/en-us/research/wp-content/uploads/2016/02/MSR-TR-2010-82.pdf
- Nogueira & Cho, cross-encoders vs bi-encoders, SBERT docs — the two-stage pattern BAAI documents. https://bge-model.com/Introduction/reranker.html
- *Adaptive term frequency normalization for BM25* (term-specific k1 beats tuned k1+b) — https://dl.acm.org/doi/pdf/10.1145/2063576.2063871
- *Lighting the Way for BRIGHT* (BM25Q detail; RRF and NAF both lift Recall@100) — https://www.alphaxiv.org/audio/2509.02558
- Chen et al., *Parameterized Dense-Sparse Fusion* (RRF vs tuned rank-score mix vs CE-on-fused-head; the CE regression) — https://arxiv.org/html/2609.22770v2
- *Calibrated Fusion for Heterogeneous Graph-Vector Retrieval* (percentile/PIT vs min-max) — https://arxiv.org/html/2603.28886
- *Rank Fusion Risk-Reward Trade-offs*, DL ACM 10.1145/3166072.3166084 — CombSUM/CombMNZ vs RRF/ISR history. https://dl.acm.org/doi/10.1145/3166072.3166084
- *Balancing the Blend* (TRF vs RRF, 4-way fusion) — https://ar5iv.labs.arxiv.org/html/2508.01405
- Chen et al., *Balancing the Blend* no — see above. TREC 2025 RAG tracks: MIT LL (decomposition + SPLADEv3 + pointwise rerank) https://trec.nist.gov/pubs/trec34/papers/MITLL.rag.pdf ; UTokyo-HitU (HyDE vector-mix, BGE-small > e5-small) https://trec.nist.gov/pubs/trec34/papers/UTokyo.rag.pdf
- Caraman et al., *SemEval-2026 Task 8* — reranker comparison table and pool-size sweep. https://arxiv.org/html/2605.12028v1
- Anserini BM25 defaults k1=0.9, b=0.4 — https://github.com/castorini/anserini/blob/master/docs/experiments/fever.md
- Elasticsearch RRF retriever: `rank_constant` default 60, per-retriever weights — https://datastudios.org/post/elasticsearch-hybrid-search-bm25-dense-vectors-rrf-and-reranking ; OpenSearch on when RRF vs score-normalisation — https://opensearch.org/blog/building-effective-hybrid-search-in-opensearch-techniques-and-best-practices
- ReBOL (active relevance observation via Bayesian optimisation; multi-query aggregation) — https://arxiv.org/pdf/2603.20513
- LLM-as-a-Judge for offline Top-K evaluation — https://arxiv.org/html/2606.22961v1 ; judge pitfalls — https://proceedings.iclr.cc/paper_files/paper/2025/file/1eb36d07ebb13be16ddbda679a95018b-Paper-Conference.pdf ; judgment-distribution inference — https://aclanthology.org/2025.findings-emnlp.1259.pdf

Model data (licence, size, scores — all read from HF API / model cards)
- BGE family report: https://huggingface.co/BAAI/bge-reranker-base ; https://huggingface.co/BAAI/bge-small-en-v1.5 ; https://bge-model.com/Introduction/reranker.html
- Qwen3 Embedding report, arXiv:2506.05176, Apache-2.0, 0.6B/1024-dim/MRL — https://arxiv.org/pdf/2506.05176 ; https://mteb-leaderboard.hf.space/models/Qwen/Qwen3-Embedding-0.6B ; Ollama build https://ollama.com/dengcao/Qwen3-Embedding-0.6B%3AF16
- MTEB leaderboard home — https://leaderboard.mteb.org/models
- nomic-embed-text-v1.5 licence/size (546.9 MB safetensors, Apache-2.0) — https://huggingface.co/api/models/nomic-ai/nomic-embed-text-v1.5

Blogs/aggregators consulted but **not** relied on for any number above (listed for the reader's
judgement): rank-fusion crate docs, indexical.dev, sesamedisk.com, korely.ai, reranker.uk,
localaimaster.com, codesota.com, multigrid.ai/dev.to (HyDE caveats — useful qualitatively, cited
as such in R6), app-lab.ai, dev.to/gabrielanhaia.

## 5. Things I could not verify

- **RRF-Lambda** as a named, citable technique. No primary source retrieved; treat as a
  vendor/blog term.
- Any **published** latency for `bge-reranker-base` or MiniLM on CPU. All latencies in this document
  are my own measurements on this box; the blog aggregates I found (e.g. "~4 ms/pair" for MiniLM-L-12
  on GPU) are GPU or vendor numbers and do not describe this machine.
- **Qwen3-Embedding-0.6B CPU latency** and its quality *on this corpus* — not measured.
- **MTEB macro scores** for the small encoders comparable to the 64.33 / 70.58 MMTEB-multilingual
  figures. The table in R5 is my own mean over 27 retrieval tasks and is internally consistent but
  not a published leaderboard number. Do not quote it as one.
- Whether the **GEMMA/embedding-model families** from the 2025–26 leaderboards that topped the
  aggregate tables (`harrier-oss-v1`, `KaLM-Embedding-Gemma3`, `jina-embeddings-v5-text-nano`) are
  viable at CPU scale here. They appear on the MMTEB leaderboard but I did not retrieve sizes,
  licences or latency, so I am not recommending them.
