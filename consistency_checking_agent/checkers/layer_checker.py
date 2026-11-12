from ..rule_loader import layer_of, allowed_edge_set

def check_layers(lifted_edges, arch_graph, rules):
    nodes = {n.id: n for n in arch_graph.nodes}
    order = {name: i for i, name in enumerate(rules.layers)}
    allowed = allowed_edge_set(rules)
    out = []
    for e in lifted_edges:
        s, t = e["source_id"], e["target_id"]
        if (s, t) in allowed or s not in nodes or t not in nodes:
            continue
        sl, tl = layer_of(nodes[s], rules), layer_of(nodes[t], rules)
        if sl not in order or tl not in order:
            continue
        delta = order[tl] - order[sl]
        kind = None
        evidence = ""
        if delta < 0 and not rules.allow_back_call:
            kind = "back_call"
            evidence = f"Layer order violation: {sl} -> {tl} is an upward dependency"
        elif delta > 1 and not rules.allow_skip:
            kind = "layer_skipping"
            skipped = rules.layers[order[sl] + 1:order[tl]]
            evidence = f"Layer order {sl} -> {tl}; skipped: {', '.join(skipped)}"
        if kind:
            out.append({**e, "violation_type": kind, "evidence_rule": evidence})
    return out
