# Tool-router Stage 1: is BM25 the right shortlister? — Research verdict
2026-09-24. Research base: StackOne 270-tool benchmark (2,700 queries), Stacklok MCP Optimizer
(2,792 tools vs Anthropic Tool Search), RAG-MCP (arXiv 2505.03275, with published critique),
MCP-Zero (2506.01056), ToolRerank (2403.06551), BEIR hybrid-search literature, SPLADE
latency studies (2511.22263), fastembed cross-encoder/ColBERT latency profiles.

## Verdict up front
**BM25-alone is NOT the industry standard for tool retrieval at your scale — hybrid
BM25 + local dense embeddings, fused with Reciprocal Rank Fusion, is.** And it is a
cheap upgrade on this machine because Ollama + nomic-embed-text are already running
for OpenViking. The pipeline becomes:

    Stage 0 (existing, free): BM25 shortlist  ─┐
    Stage 0.5 (new, ~5-15ms): nomic dense shortlist ─┤→ RRF fuse → top-20
    Stage 1 (existing, ~100ms): Laya rerank picks winner + needs-tool gate
    Stage 2 (existing): confidence gating → card

This mirrors the industry-standard three-stage pattern: high-recall candidate
generation (hybrid) → precision rerank (Laya = your cross-encoder analog) → decision.

## The numbers that decide it
| Method | Top-1 acc | Top-5 acc | Latency | Source |
|---|---|---|---|---|
| BM25 only | 14% | 87% | <1ms | StackOne, 270 tools |
| BM25 + TF-IDF hybrid | 21% | 90% | <1ms | StackOne |
| Embedding search | 38% | 85% | 50-200ms | StackOne |
| Reranker | 40%+ | 90%+ | 200-500ms | StackOne |
| BM25 only (2,792 tools) | 34% | — | — | Stacklok |
| Hybrid semantic+BM25 | **94%** | — | 5.75s (incl. LLM) | Stacklok |
| All-tools-in-prompt | 13.6% | — | — | RAG-MCP |
| Retrieval-first (semantic) | 43.1% | — | — | RAG-MCP |

Key fact: BM25's top-5 recall is excellent (87-90%) but top-1 is terrible at scale —
it finds the right skill but can't rank it first. That is EXACTLY the failure our Laya
rerank layer exists to fix, but Laya can only reorder what BM25 hands it. If the right
skill isn't in BM25's shortlist, Laya never sees it. Hybrid raises the chance the
right candidate is IN the shortlist; Laya then guarantees the right one WINS it.

Why BM25 degrades: verb saturation (create/get/list appear in hundreds of names →
IDF ≈ 0), uniformly short descriptions collapse BM25's length-normalization edge,
and zero vocabulary bridging ("notify the team" ↛ slack_send_message).

## Why NOT the other candidates
- **SPLADE (learned sparse)**: beats BM25 on benchmarks but 100-300ms/query even
  with precomputation tricks, weaker on out-of-vocabulary tokens (your error codes,
  tool names), heavier to maintain. Not worth it at 665 items.
- **ColBERT late-interaction**: reranker-class quality, but its value is at 1k-10k
  candidate scale and high QPS. We have 665 items and one user. Over-engineering.
- **Cross-encoder reranker (ms-marco MiniLM)**: the classic choice, 20-50ms.
  LAYA ALREADY IS THIS LAYER — a trained relevance model over the shortlist, with
  calibrated confidence, for free. Adding a MiniLM cross-encoder would duplicate it.
- **Pure dense (drop BM25)**: benchmark-settled as worse than hybrid — dense alone
  misses exact identifiers (skill names, MCP names, error strings). Keep BM25.
- **Hierarchical discovery**: only pays at 500+ MCP servers / thousands of tools.
  Revisit if the fleet passes ~1,500 active tools.

## Scale check for THIS router
Current index: 665 items (529 skills, 60 plugin-skills, 36 agents, 30 commands,
10 MCPs). StackOne-style table puts 200-500 items in "hybrid BM25+embedding → 85-90%"
territory. We are just past that boundary and growing (984 new skills added this
week — the next index rebuild will cross 1,600). The upgrade is due, not premature.

## Implementation plan (lazy version, ~80 lines)
1. At index-build: embed each item once — `curl localhost:11434/api/embeddings` with
   nomic-embed-text (768d), store as `index.json` extra field. 665 × ~20ms ≈ 15s
   one-time, only re-embed changed items (content hash).
2. At query: embed the prompt (~10ms), cosine vs all items (665 × 768 dot products
   = trivial, no ANN index needed at this scale — numpy one-liner), take top-50.
3. RRF-fuse BM25 top-50 + dense top-50 (k=60), take top-20 → existing Laya rerank.
4. Fail-open: Ollama down → BM25-only path unchanged (circuit breaker, same pattern
   as laya_rerank).
Optional micro-tune later (Phase-1-style BM25 polish, zero infrastructure):
field-weight tool names above descriptions, de-weight create/get/list verbs —
StackOne showed this alone moves 14%→21% top-1.

## Sources
- StackOne benchmark + MCPProxy analysis: mcpproxy.app/blog/2026-03-15-beyond-bm25-tool-discovery
- Stacklok MCP Optimizer 94% vs 34%: same article
- RAG-MCP: arxiv.org/abs/2505.03275 (+ Pith critique noting its statistical flaws —
  its direction is corroborated independently by StackOne/Stacklok numbers)
- MCP-Zero (active discovery, 98% token savings): arxiv.org/abs/2506.01056
- ToolRerank (adaptive truncation + hierarchy-aware rerank): arxiv.org/abs/2403.06551
- Hybrid search craft (RRF k=60, score-incompatibility): dataaspirant.com/blog/hybrid-search
- SPLADE latency: arxiv.org/abs/2511.22263 (6x BM25 query latency)
- ColBERT vs cross-encoder latency profiles: towardsdatascience.com cross-encoders reranking
