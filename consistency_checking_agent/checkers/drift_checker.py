from ..rule_loader import allowed_edge_set

def check_drift(lifted_edges, arch_graph, rules, mapping):
    designed = {(e.source_id, e.target_id) for e in arch_graph.edges}
    allowed = allowed_edge_set(rules)
    actual = {(e["source_id"], e["target_id"]) for e in lifted_edges}
    candidates, violations = [], []
    for e in lifted_edges:
        pair = (e["source_id"], e["target_id"])
        if pair not in designed and pair not in allowed:
            candidates.append({"drift_kind": "undeclared_edge", "source_id": pair[0], "target_id": pair[1],
                               "reason": "Dependency exists in code but is not declared in the design graph",
                               "confidence": e.get("mapping_score", 0.0), "needs_human_review": True,
                               "evidence": e})
    for source, target in sorted(designed - actual):
        candidates.append({"drift_kind": "missing_edge", "source_id": source, "target_id": target,
                           "reason": "Design dependency was not observed in the source-code graph",
                           "confidence": 1.0, "needs_human_review": True})
    for item in mapping:
        if item.arch_node_id is None:
            candidates.append({"drift_kind": "unmapped_node", "source_id": item.code_node_id, "target_id": None,
                               "reason": "Source-code node could not be mapped unambiguously to an architecture node",
                               "confidence": item.score, "needs_human_review": True})
    return violations, candidates
