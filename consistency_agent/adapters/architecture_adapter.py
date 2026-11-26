"""Adapter: ArchitectureGraph (Architecture Extraction Agent) -> DesignModel nội bộ."""
from __future__ import annotations

import logging
from typing import Any, Optional

import networkx as nx

from ..schemas.inputs import ArchitectureGraphIn, RuleSet
from ..schemas.internal import DesignComponent, DesignModel

log = logging.getLogger(__name__)

# Các loại node mô tả bối cảnh hoặc hạ tầng (người dùng, hệ thống ngoài, CSDL, hàng đợi, cache)
# -> mặc định KHÔNG kỳ vọng có package tương ứng trong mã nguồn, trừ khi có gợi ý ánh xạ
# (properties.code_paths của AEA hoặc RuleSet.component_mapping).
NOT_IN_CODE_TYPES = {"actor", "external_system", "person", "external", "database", "queue", "cache"}
HINT_KEYS = ("code_paths", "code_path", "module", "modules", "package", "packages")


def _hints(props: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in HINT_KEYS:
        val = props.get(key)
        if isinstance(val, str) and val.strip():
            out.append(val.strip())
        elif isinstance(val, list):
            out.extend(str(v).strip() for v in val if str(v).strip())
    # chuẩn hóa "src/controllers" -> "controllers" dạng dotted
    return [h.replace("\\", "/").strip("/").replace("/", ".") for h in out]


def to_design_model(data: Any, rules: Optional[RuleSet] = None) -> DesignModel:
    """Chuẩn hóa đầu ra AEA. ``data`` có thể là dict, JSON đã parse hoặc model Pydantic của AEA."""
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    if isinstance(data, dict) and "nodes" not in data:
        # Chấp nhận dạng bọc: {"final_graph": {...}} (state LangGraph của AEA) hoặc {"payload": {...}}
        for key in ("final_graph", "architecture_graph", "payload"):
            if isinstance(data.get(key), dict):
                data = data[key]
                break
    graph_in = ArchitectureGraphIn.model_validate(data)
    rules = rules or RuleSet()

    layer_of: dict[str, str] = {}
    for spec in rules.layers:
        for comp_id in spec.components:
            layer_of[comp_id] = spec.name

    warnings = list(graph_in.warnings)
    components: dict[str, DesignComponent] = {}
    g = nx.DiGraph()
    for n in graph_in.nodes:
        if n.id in components:
            warnings.append(f"Node thiết kế trùng id '{n.id}' - giữ bản đầu tiên.")
            continue
        layer = layer_of.get(n.id) or n.properties.get("layer")
        comp = DesignComponent(
            id=n.id,
            name=n.name,
            type=n.type,
            layer=str(layer) if layer else None,
            confidence=n.confidence,
            expected_in_code=(n.type not in NOT_IN_CODE_TYPES or bool(_hints(n.properties))
                              or n.id in rules.component_mapping),
            code_hints=_hints(n.properties),
            evidence=n.evidence,
        )
        components[n.id] = comp
        g.add_node(n.id)

    for e in graph_in.edges:
        if e.source_id not in components or e.target_id not in components:
            warnings.append(f"Bỏ cạnh thiết kế {e.source_id}->{e.target_id}: thiếu node.")
            continue
        if e.source_id == e.target_id:
            continue
        if g.has_edge(e.source_id, e.target_id):
            g[e.source_id][e.target_id]["confidence"] = max(g[e.source_id][e.target_id]["confidence"], e.confidence)
            continue
        g.add_edge(e.source_id, e.target_id, relationship_type=e.relationship_type,
                   confidence=e.confidence, label=e.label or "")

    log.debug("DesignModel: %d components, %d edges", len(components), g.number_of_edges())
    return DesignModel(system_name=graph_in.system_name, components=components, graph=g, warnings=warnings)
