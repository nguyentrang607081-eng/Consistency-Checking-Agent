"""Hợp đồng dữ liệu ĐẦU VÀO của Consistency Checking Agent (CCA).

Có 4 nguồn đầu vào:

1. ``ArchitectureGraphIn``  - đầu ra của Architecture Extraction Agent (AEA).
   Mô phỏng đúng ``schema.py`` trong repo brusssss1/Architecture-Extraction-Agent
   (ArchitectureGraph / GraphNode / GraphEdge), nhưng nới lỏng kiểu ``type`` và
   cho phép trường lạ (``extra="allow"``) để không vỡ khi nhóm AEA bổ sung trường.

2. ``SourceCodeEnvelopeIn`` - đầu ra của Source Code Analysis Agent (SCA).
   Mô phỏng đúng ``schema.py`` trong repo dtth0729/source_code_analysis_agent
   (SourceCodeEnvelope / Payload / CodeDependency / Evidence).

3. ``RuleSet``  - bộ luật kiến trúc. Hiện chưa agent nào sinh ra định dạng này,
   nên CCA tự định nghĩa (người dùng khai báo hoặc Coordinator chuyển tới).

4. ``StrategyDirective`` - chiến lược do Coordinator gửi xuống. Khớp với
   ``build_for("consistency_checking", ...)`` trong repo tranganhne/coordinator-agent:
   ``{"confidence_threshold": float, "target_modules": [...]}`` và mở rộng thêm
   ``scope`` (tùy chọn).
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ------------------------------------------------------------------ AEA
class DesignNodeIn(BaseModel):
    """Một node trong ArchitectureGraph của AEA."""

    model_config = ConfigDict(extra="allow")

    id: str
    name: str
    # AEA dùng Literal["service","database","ui","queue","cache","external_system","actor"].
    # CCA nhận mọi chuỗi để tương thích về sau (vd: "component", "layer").
    type: str = "service"
    c4_level: str = "container"
    properties: dict[str, Any] = Field(default_factory=dict)
    source: str = "diagram"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: Optional[str] = None


class DesignEdgeIn(BaseModel):
    """Một cạnh trong ArchitectureGraph của AEA."""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    source_id: str
    target_id: str
    relationship_type: str = "dependency"
    label: Optional[str] = None
    source: str = "diagram"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ArchitectureGraphIn(BaseModel):
    """Đồ thị kiến trúc thiết kế (đầu ra AEA)."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0.0"
    system_name: str = "Extracted System"
    nodes: list[DesignNodeIn] = Field(default_factory=list)
    edges: list[DesignEdgeIn] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ SCA
class CodeEvidenceIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    file_path: str
    line_start: int = 0
    line_end: int = 0


class CodeDependencyIn(BaseModel):
    """Một phụ thuộc trong payload của SCA.

    Lưu ý: SCA hiện đặt ``from_module`` = tên file không có đuôi (``Path.stem``),
    nên nhiều file trùng tên (vd ``mapper.py``) sẽ trùng ``from_module``. CCA vì vậy
    dựng lại tên module đầy đủ từ ``evidence.file_path`` (xem ``code_adapter``).
    """

    model_config = ConfigDict(extra="allow")

    from_module: str
    to_module: str
    dependency_type: str = "import"
    evidence: CodeEvidenceIn


class CodePayloadIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    dependencies: list[CodeDependencyIn] = Field(default_factory=list)


class SourceCodeEnvelopeIn(BaseModel):
    """Phong bì JSON của SCA."""

    model_config = ConfigDict(extra="allow")

    schema_version: str = "1.0"
    project_id: str = "unknown-project"
    run_id: str = "unknown-run"
    agent_name: str = "Source Code Analysis"
    status: Literal["SUCCESS", "PARTIAL", "FAILED"] = "SUCCESS"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[str] = Field(default_factory=list)  # danh sách file đã quét
    payload: CodePayloadIn = Field(default_factory=CodePayloadIn)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ RULES
RuleKind = Literal["forbid", "allow", "require"]


class ArchitectureRule(BaseModel):
    """Một luật kiến trúc.

    ``source`` / ``target`` là *selector*:
      - ``"<component_id>"``  : id của component thiết kế (vd ``"controllers"``)
      - ``"layer:<tên>"``     : mọi component thuộc layer đó
      - ``"external:<prefix>"``: thư viện ngoài (vd ``"external:sqlalchemy"``), chỉ dùng cho target
      - ``"*"``               : mọi component
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    kind: RuleKind
    source: str
    target: str
    severity: Optional[Literal["low", "medium", "high", "critical"]] = None
    description: str = ""


class LayerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    components: list[str] = Field(default_factory=list)


class RuleSet(BaseModel):
    """Bộ luật kiến trúc + ánh xạ khai báo (tùy chọn)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1.0"
    # Chính sách cho dependency KHÔNG có trong thiết kế và không khớp luật nào:
    #  - "design_only": coi là vi phạm (unexpected_dependency)
    #  - "unspecified": ghi nhận trạng thái CHƯA XÁC ĐỊNH, cần người xác nhận (mặc định)
    #  - "allow"      : cho phép
    default_policy: Literal["design_only", "unspecified", "allow"] = "unspecified"
    # Layers xếp từ NGOÀI vào TRONG (trên xuống dưới). Layer ngoài được phụ thuộc layer trong.
    layers: list[LayerSpec] = Field(default_factory=list)
    # "inward": được phụ thuộc mọi layer bên trong; "strict": chỉ layer kề dưới.
    layer_policy: Literal["inward", "strict"] = "inward"
    # Ánh xạ khai báo: design_component_id -> danh sách package mã nguồn (vd ["controllers"])
    component_mapping: dict[str, list[str]] = Field(default_factory=dict)
    # Tự suy luật từ thiết kế (cạnh thiết kế = được phép, cạnh ngược = nghi vấn)
    derive_rules_from_design: bool = True
    # Package mã nguồn không cần đối chiếu (vd "main" - điểm khởi chạy, "scripts")
    ignore_code_packages: list[str] = Field(default_factory=list)
    rules: list[ArchitectureRule] = Field(default_factory=list)


# ------------------------------------------------------------------ COORDINATOR
class StrategyDirective(BaseModel):
    """Chỉ thị chiến lược từ Coordinator (cấp hệ thống). Mọi trường đều tùy chọn."""

    model_config = ConfigDict(extra="allow")

    scope: Optional[Literal["full", "incremental", "auto"]] = None
    target_modules: list[str] = Field(default_factory=list)
    confidence_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    deep_matching: Optional[bool] = None
    llm_review: Optional[bool] = None   # Coordinator gợi ý bật/tắt kiểm tra lại bằng LLM


class CheckContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    project_id: str = ""   # rỗng -> lấy project_id trong envelope của SCA
    scan_id: str = ""      # rỗng -> lấy run_id của SCA hoặc sinh theo thời gian
    commit_hash: str = ""
