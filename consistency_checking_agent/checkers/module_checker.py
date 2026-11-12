from ..rule_loader import allowed_edge_set, module_of

def check_cross_module(lifted_edges, rules):
    allowed = allowed_edge_set(rules)
    out = []
    for e in lifted_edges:
        s, t = e["source_id"], e["target_id"]
        sm, tm = module_of(s, rules), module_of(t, rules)
        if sm and tm and sm != tm and (s, t) not in allowed:
            out.append({**e, "violation_type": "cross_module",
                        "evidence_rule": f"Dependency crosses module boundary: {sm} -> {tm}"})
    return out
