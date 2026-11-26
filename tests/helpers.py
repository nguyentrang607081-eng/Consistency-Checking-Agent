"""Tiện ích dựng dữ liệu test đúng định dạng đầu ra của AEA và SCA."""
from __future__ import annotations

from typing import Iterable, Optional


def design_graph(nodes: Iterable, edges: Iterable[tuple[str, str]], system: str = "Test System") -> dict:
    """nodes: list id | (id, name) | (id, name, type) | (id, name, type, confidence)."""
    out_nodes = []
    for n in nodes:
        if isinstance(n, str):
            n = (n, n)
        nid, name = n[0], n[1]
        ntype = n[2] if len(n) > 2 else "service"
        conf = n[3] if len(n) > 3 else 1.0
        out_nodes.append({"id": nid, "name": name, "type": ntype, "c4_level": "component", "properties": {},
                          "source": "diagram", "confidence": conf, "evidence": None})
    out_edges = [{"id": f"e{i}", "source_id": s, "target_id": t, "relationship_type": "dependency",
                  "label": None, "source": "diagram", "confidence": 1.0} for i, (s, t) in enumerate(edges)]
    return {"schema_version": "1.0.0", "system_name": system, "nodes": out_nodes, "edges": out_edges,
            "warnings": []}


def code_envelope(modules: dict[str, list[str]], project: str = "proj", status: str = "SUCCESS",
                  run_id: str = "run-1", root: str = "/repo/proj/src") -> dict:
    """modules: {"api.views": ["service.core", "fastapi"], ...} -> SourceCodeEnvelope giống SCA.

    Tự tạo __init__.py cho mọi package; đường dẫn tuyệt đối như SCA thật.
    """
    files: dict[str, str] = {}
    for mod in modules:
        parts = mod.split(".")
        for i in range(1, len(parts)):
            pkg = "/".join(parts[:i])
            files[f"{root}/{pkg}/__init__.py"] = ".".join(parts[:i])
        files[f"{root}/{'/'.join(parts)}.py"] = mod
    deps = []
    for mod, imports in modules.items():
        path = f"{root}/{mod.replace('.', '/')}.py"
        for line, imp in enumerate(imports, start=1):
            deps.append({"from_module": mod.split(".")[-1], "to_module": imp, "dependency_type": "import",
                         "evidence": {"file_path": path, "line_start": line, "line_end": line}})
    return {"schema_version": "1.0", "project_id": project, "run_id": run_id, "agent_name": "Source Code Analysis",
            "status": status, "confidence": 0.95, "evidence": sorted(files), "payload": {"dependencies": deps},
            "warnings": [], "errors": []}


def rules(*items: dict, default_policy: str = "unspecified", layers: Optional[list] = None, **kw) -> dict:
    return {"default_policy": default_policy, "layers": layers or [], "rules": list(items), **kw}


def by_type(report, vtype: str) -> list:
    return [f for f in report.findings if f.violation_type == vtype]


def mapping(report, design_id: str):
    return next(m for m in report.mappings if m.design_id == design_id)
