import networkx as nx

def check_cycles(lifted_edges):
    graph = nx.DiGraph()
    graph.add_edges_from((e["source_id"], e["target_id"]) for e in lifted_edges)
    out = []
    for component in sorted(nx.strongly_connected_components(graph), key=lambda c: sorted(c)):
        if len(component) < 2:
            continue
        members = sorted(component)
        internal = sorted((e for e in lifted_edges if e["source_id"] in component and e["target_id"] in component),
                          key=lambda e: (e["source_id"], e["target_id"], e.get("file_path") or "", e.get("line_number") or 0))
        if internal:
            e = internal[0]
            out.append({**e, "violation_type": "circular_dependency",
                        "evidence_rule": "Strongly connected component: " + " -> ".join(members)})
    return out
