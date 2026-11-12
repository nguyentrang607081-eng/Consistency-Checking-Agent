# Algorithms

## Mapping
1. Exact ID/name after normalization (score 1.0).
2. File-path/layer/module rule (score 0.95).
3. RapidFuzz ratio; default threshold 0.85. Ties within 0.02 remain unmapped for human review.
4. Optional LLM mapping is not enabled in the initial deterministic implementation.

## Edge lifting
A CodeEdge is lifted only if both endpoints have an unambiguous architecture mapping. The original file path, line number, snippet and minimum endpoint mapping score are retained.

## Rule checks
- Layer order is configured from upper to lower layers. A reverse edge is back_call; a forward edge that skips layers is layer_skipping.
- Cross-module checks require both modules to be explicitly known.
- NetworkX strongly connected components detect directed dependency cycles.
- Code edges absent from design are undeclared_edge candidates; design edges absent from code are missing_edge candidates. Neither is automatically a violation.

## Metrics
- mapping_coverage = mapped code nodes / all code nodes.
- consistency_score = valid lifted edges / all lifted edges.
- Empty denominators are handled safely.
