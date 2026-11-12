"""Deterministic code-to-architecture mapping and edge lifting."""
import re
from rapidfuzz import fuzz, process
from .schema import ArchitectureGraph, CodeGraph, MappingEntry

def normalize(value: str) -> str:
    value = value.lower().replace("\\", "/")
    value = re.sub(r"[^a-z0-9]+", "", value)
    for suffix in ("service", "controller", "repository", "impl"):
        if value.endswith(suffix) and len(value) > len(suffix) + 2:
            value = value[:-len(suffix)]
            break
    return value

def map_code_to_architecture(code_graph: CodeGraph, arch_graph: ArchitectureGraph, min_score: float = .85) -> list[MappingEntry]:
    arch_nodes = arch_graph.nodes
    by_id = {n.id: n for n in arch_nodes}
    by_name: dict[str, list] = {}
    for n in arch_nodes:
        by_name.setdefault(normalize(n.name), []).append(n)
        by_name.setdefault(normalize(n.id), []).append(n)
    choices = {n.id: n for n in arch_nodes}
    results = []
    for code in code_graph.nodes:
        path = (code.file_path or code.id).replace("\\", "/").lower()
        candidates = []
        exact = by_id.get(code.id)
        if exact:
            results.append(MappingEntry(code_node_id=code.id, arch_node_id=exact.id, score=1.0, method="exact"))
            continue
        normalized_names = [normalize(code.name), normalize(code.id.rsplit("/", 1)[-1].rsplit(".", 1)[0])]
        exact_matches = []
        for key in normalized_names:
            exact_matches.extend(by_name.get(key, []))
        exact_matches = list({n.id: n for n in exact_matches}.values())
        if len(exact_matches) == 1:
            results.append(MappingEntry(code_node_id=code.id, arch_node_id=exact_matches[0].id, score=1.0, method="exact"))
            continue
        # Path-based layer/module evidence has priority over fuzzy matching.
        path_tokens = [x for x in path.split("/") if x and x not in {"src", "app"}]
        for node in arch_nodes:
            layer = str(node.properties.get("layer", node.id.split(".", 1)[0])).lower()
            module = str(node.properties.get("module", "")).lower()
            name = normalize(node.name)
            if layer in path_tokens and name and any(name in normalize(t) or normalize(t) in name for t in path_tokens):
                candidates.append((node, .95))
            elif module and module in path_tokens and name and any(name in normalize(t) for t in path_tokens):
                candidates.append((node, .95))
        unique = {n.id: (n, s) for n, s in candidates}
        if len(unique) == 1:
            n, score = next(iter(unique.values()))
            results.append(MappingEntry(code_node_id=code.id, arch_node_id=n.id, score=score, method="path_rule"))
            continue
        # Fuzzy matching is deterministic; ties are deliberately left unresolved.
        query = normalize(code.name or code.id)
        scored = sorted(((fuzz.ratio(query, normalize(n.name)) / 100, n) for n in arch_nodes),
                         key=lambda x: (-x[0], x[1].id))
        if scored and scored[0][0] >= min_score and (len(scored) == 1 or scored[0][0] - scored[1][0] > .02):
            score, node = scored[0]
            results.append(MappingEntry(code_node_id=code.id, arch_node_id=node.id, score=score, method="fuzzy"))
        else:
            best = scored[0][0] if scored else 0.0
            results.append(MappingEntry(code_node_id=code.id, arch_node_id=None, score=best, method="fuzzy", needs_human_review=True))
    return results

def lift_edges(code_graph: CodeGraph, mapping: list[MappingEntry]) -> list[dict]:
    lookup = {m.code_node_id: m for m in mapping}
    lifted = []
    for edge in code_graph.edges:
        src, dst = lookup.get(edge.source_id), lookup.get(edge.target_id)
        if src and dst and src.arch_node_id and dst.arch_node_id:
            lifted.append({"source_id": src.arch_node_id, "target_id": dst.arch_node_id,
                           "mapping_score": min(src.score, dst.score), "file_path": edge.file_path,
                           "line_number": edge.line_number, "code_snippet": edge.code_snippet,
                           "relationship_type": edge.relationship_type})
    return lifted
