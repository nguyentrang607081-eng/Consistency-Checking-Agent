# Coordinator and Adaptive Recommendation integration

## CCA boundary

CCA accepts `ConsistencyInput` and returns `ConsistencyReport` from the technical specification. The coordinator should send:
- `architecture_graph`: Architecture Extraction Agent schema version 1.0.0;
- `code_graph`: the agreed Code Analysis Agent graph (the current adapter follows the example in the CCA specification);
- optional `rules`, `confidence_threshold`, and `human_decisions`.

## Important contract difference

The currently referenced Adaptive Recommendation Agent repository exposes `RecommendationRequest` with `project_id`, `run_id`, `architecture_rules`, and `issues` (each a `ConsistencyIssue`). It does **not** directly expose the `ViolationItem[]` contract described as the intended CCA hand-off in the CCA specification.

Therefore, do not pass CCA's `violations` array directly into that repository's API. The Coordinator needs an explicit, versioned adapter that maps:
- CCA `violation_id` -> recommendation `issue_id`;
- CCA `source_id` / `target_id` -> `source_module` / `target_module`;
- CCA `severity` -> recommendation priority (with a documented mapping);
- CCA `confidence` -> `confidence`;
- CCA file/line/snippet/evidence_rule -> recommendation `evidence` and `rule_description`.

The adapter must preserve the original CCA report for auditability and must not silently discard unmapped fields. Confirm the mapping with the owners of Agent 5 before claiming end-to-end contract compatibility.

## API

Start CCA:

```bash
uvicorn consistency_checking_agent.interface:app --reload
```

Health check: `GET /health`

Analysis: `POST /check-consistency` with a JSON body conforming to `ConsistencyInput`.

Interactive API documentation: `/docs`.
