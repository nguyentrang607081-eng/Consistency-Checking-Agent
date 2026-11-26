"""Mô hình dữ liệu NỘI BỘ (sau khi adapter chuẩn hóa).

Tách riêng khỏi schema đầu vào để khi AEA / SCA đổi định dạng, ta chỉ cần sửa adapter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

import networkx as nx

Resolution = Literal["internal", "unresolved_internal", "external"]


@dataclass
class DesignComponent:
    id: str
    name: str
    type: str
    layer: Optional[str] = None
    confidence: float = 1.0          # độ tin cậy do AEA gán (text/LLM thường < 1)
    expected_in_code: bool = True    # actor / external_system thì không kỳ vọng có trong code
    code_hints: list[str] = field(default_factory=list)  # properties.code_paths / module
    evidence: Optional[str] = None   # đoạn văn bản nguồn AEA trích ra (dùng cho RAG)


@dataclass
class DesignModel:
    """Kiến trúc thiết kế: đồ thị có hướng giữa các component."""

    system_name: str
    components: dict[str, DesignComponent]
    graph: nx.DiGraph  # cạnh có thuộc tính: relationship_type, confidence
    warnings: list[str] = field(default_factory=list)


@dataclass
class CodeEvidence:
    file_path: str
    line_start: int
    line_end: int
    raw_import: str


@dataclass
class CodeDependency:
    source_module: str            # tên module đầy đủ, vd "controllers.rest.pokemon.router"
    target: str                   # module đích đã phân giải (hoặc tên thư viện ngoài)
    resolution: Resolution
    dependency_type: str
    evidence: CodeEvidence


@dataclass
class CodeModel:
    """Kiến trúc thực tế ở mức module (chưa gộp thành component)."""

    project_id: str
    run_id: str
    source_roots: list[str]
    modules: dict[str, str]            # module_name -> file_path tương đối
    packages: set[str]                 # mọi package (kể cả package cha)
    dependencies: list[CodeDependency]
    phantom_packages: set[str]         # package được import nội bộ nhưng không tồn tại file
    status: str = "SUCCESS"
    sca_confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    @property
    def module_graph(self) -> nx.DiGraph:
        g = nx.DiGraph()
        g.add_nodes_from(self.modules)
        for d in self.dependencies:
            if d.resolution != "external":
                g.add_edge(d.source_module, d.target)
        return g
