"""Deterministic aggregation, severity assignment and scoring."""
from collections import Counter
from .schema import (ConsistencyReport, ConsistencySummary, MappingReport, ViolationItem, DriftCandidate)

def aggregate(system_name, code_graph, arch_graph, mapping, lifted_edges, raw_violations, raw_candidates, warnings=None, errors=None):
    warnings, errors = list(warnings or []), list(errors or [])
    decisions = {(d.source_id, d.target_id): d.decision for d in getattr(code_graph, "_human_decisions", [])}
    unique = {}
    for item in raw_violations:
        key = (item["violation_type"], item["source_id"], item["target_id"], item.get("file_path"), item.get("line_number"))
        unique.setdefault(key, item)
    violations = []
    for i, (key, item) in enumerate(sorted(unique.items(), key=lambda kv: tuple(str(x) for x in kv[0])), 1):
        kind = item["violation_type"]
        severity = {"back_call": "high", "circular_dependency": "high", "layer_skipping": "medium",
                    "cross_module": "medium", "intentional_drift": "low"}[kind]
        if kind == "layer_skipping":
            evidence = item.get("evidence_rule", "")
            if evidence.count(" -> ") >= 2:
                severity = "high"
        confidence = float(item.get("mapping_score", 1.0)) * float(item.get("rule_confidence", .9 if kind == "cross_module" else 1.0))
        violations.append(ViolationItem(
            violation_id=f"VIO-{i:03d}", violation_type=kind, source_id=item["source_id"], target_id=item["target_id"],
            file_path=item.get("file_path"), line_number=item.get("line_number"), code_snippet=item.get("code_snippet"),
            severity=severity, confidence=max(0.0, min(1.0, confidence)),
            human_decision=item.get("human_decision"), evidence_rule=item.get("evidence_rule", "Architecture rule violation")
        ))
    drift_candidates = []
    for i, item in enumerate(sorted(raw_candidates, key=lambda x: (x["drift_kind"], x["source_id"], x.get("target_id") or "")), 1):
        drift_candidates.append(DriftCandidate(candidate_id=f"DRF-{i:03d}", **{k:v for k,v in item.items() if k in DriftCandidate.model_fields}))
    mapped = [m for m in mapping if m.arch_node_id is not None]
    total_nodes = len(code_graph.nodes)
    coverage = len(mapped) / total_nodes if total_nodes else 0.0
    unmatched = sorted(n.id for n in arch_graph.nodes if n.id not in {m.arch_node_id for m in mapped})
    mapping_report = MappingReport(mapped=mapping, unmapped_code=[m.code_node_id for m in mapping if m.arch_node_id is None],
                                    unmatched_architecture=unmatched, mapping_coverage=coverage)
    invalid_pairs = {(v.source_id, v.target_id) for v in violations}
    valid_count = sum(1 for e in lifted_edges if (e["source_id"], e["target_id"]) not in invalid_pairs)
    score = valid_count / len(lifted_edges) if lifted_edges else 1.0
    by_type = dict(Counter(v.violation_type for v in violations))
    summary = ConsistencySummary(total_code_edges=len(code_graph.edges), mapped_edges=len(lifted_edges),
                                 total_violations=len(violations), by_type=by_type, drift_candidate_count=len(drift_candidates))
    return ConsistencyReport(system_name=system_name, summary=summary, violations=violations,
                             drift_candidates=drift_candidates, mapping_report=mapping_report,
                             consistency_score=score, warnings=warnings, errors=errors)
