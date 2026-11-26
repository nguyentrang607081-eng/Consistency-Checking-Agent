"""Hợp đồng dữ liệu ĐẦU RA của Consistency Checking Agent.

``ConsistencyReport`` là đầu vào trực tiếp cho Reporting Agent (và cho Coordinator,
qua adapter ``to_coordinator_input``). Mọi model đều serialize được sang JSON bằng
``model_dump(mode="json")`` / ``model_dump_json()``.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

Severity = Literal["low", "medium", "high", "critical"]

ViolationType = Literal[
    "forbidden_dependency",   # vi phạm luật forbid do người dùng khai báo
    "layer_violation",        # phụ thuộc ngược chiều layer
    "reverse_dependency",     # phụ thuộc ngược chiều so với cạnh trong thiết kế
    "cyclic_dependency",      # chu trình phụ thuộc giữa các component
    "unexpected_dependency",  # phụ thuộc có trong code nhưng không có trong thiết kế (divergence)
    "missing_dependency",     # phụ thuộc có trong thiết kế/luật require nhưng không thấy trong code (absence)
    "missing_component",      # component thiết kế không tìm thấy trong code
    "unexpected_component",   # package trong code không được mô tả trong kiến trúc
    "uncertain_mapping",      # ánh xạ component chưa chắc chắn, cần người xác nhận
]

FindingStatus = Literal[
    "confirmed",     # đủ tin cậy để kết luận
    "needs_review",  # tin cậy thấp -> cần người xác nhận (human-in-the-loop)
    "undetermined",  # luật không đủ rõ để kết luận
    "accepted",      # người dùng đã chấp nhận (lưu trong Knowledge) -> không tính là vi phạm mở
]

DetectedBy = Literal["rule_engine", "graph_matching", "similarity"]

# 4 kịch bản đối chiếu (+ "uncertain" khi chưa đủ bằng chứng để xếp loại)
Scenario = Literal[
    "match",      # 1. Cặp dữ liệu khớp hoàn toàn (thiết kế == code)
    "wrong",      # 2. Code SAI so với thiết kế (phụ thuộc sai chiều / bị cấm / không có trong thiết kế)
    "missing",    # 3. Code THIẾU so với thiết kế (component / phụ thuộc chưa hiện thực)
    "junk",       # 4. Code RÁC: có trong code nhưng thiết kế không có (code chết, package ngoài kiến trúc, import ma)
    "uncertain",  # chưa đủ bằng chứng (ánh xạ chưa chắc) -> cần người / LLM xác nhận
]


class EvidenceItem(BaseModel):
    file_path: str = ""
    line_start: int = 0
    line_end: int = 0
    statement: str = ""  # vd "import repositories.relational_db (resolved: internal)"


class ConfidenceBreakdown(BaseModel):
    """Các yếu tố tạo nên confidence_score (nhân với nhau)."""

    mapping: float = 1.0
    rule_clarity: float = 1.0
    evidence: float = 1.0
    data_completeness: float = 1.0
    formula: str = "mapping * rule_clarity * evidence * data_completeness"


class LLMReview(BaseModel):
    """Kết quả rà soát bằng RAG + few-shot + LLM (hoặc bộ suy luận few-shot cục bộ khi không có LLM).

    Đây là Ý KIẾN THỨ HAI, tách riêng khỏi confidence_score (vốn tính bằng công thức giải thích được).
    """

    provider: str                      # "local-fewshot" | "openai_compat"
    model: str
    is_llm: bool                       # False với bộ suy luận cục bộ (không phải mô hình ngôn ngữ)
    verdict: Literal["confirm", "reject", "uncertain"]
    scenario: Scenario
    confidence: float                  # độ tin cậy do reviewer tự báo (LLM) / tỷ lệ phiếu (local)
    rationale: str
    suggested_fix: str = ""
    retrieved_docs: list[str] = Field(default_factory=list)   # id tài liệu RAG đã đưa vào ngữ cảnh
    cited_docs: list[str] = Field(default_factory=list)       # id tài liệu reviewer trích dẫn
    few_shot_ids: list[str] = Field(default_factory=list)
    cached: bool = False
    status_changed: bool = False       # True nếu policy "resolve" đã đổi status nhờ review này


class Finding(BaseModel):
    finding_id: str
    fingerprint: str  # ổn định giữa các lần quét: dùng để nhớ quyết định của người dùng
    violation_type: ViolationType
    severity: Severity
    confidence_score: float
    confidence_breakdown: ConfidenceBreakdown
    status: FindingStatus
    detected_by: DetectedBy
    rule_id: str
    description: str
    source_component: str = ""
    target_component: str = ""
    expected_dependency: str = ""  # vd "controllers -> usecases" hoặc "(không có)"
    actual_dependency: str = ""
    file_path: str = ""
    line_number: int = 0
    evidence: list[EvidenceItem] = Field(default_factory=list)
    recommended_action: str = ""
    carried_over: bool = False  # True: lấy lại từ lần quét trước (quét incremental)
    scenario: Optional[Scenario] = None          # 1 trong 4 kịch bản (gán bởi scenarios.classify_finding)
    scenario_detail: str = ""                    # vd junk: orphan / undocumented / phantom
    llm_review: Optional[LLMReview] = None


class MappingCandidate(BaseModel):
    code_package: str
    score: float


class ComponentMapping(BaseModel):
    design_id: str
    design_name: str
    design_type: str
    code_packages: list[str] = Field(default_factory=list)
    module_count: int = 0
    confidence: float = 0.0
    status: Literal["confirmed", "uncertain", "unmapped", "not_applicable"]
    method: str = ""  # explicit / knowledge / name+structure / name_only / structure_only ...
    name_score: float = 0.0
    structure_score: Optional[float] = None
    alternatives: list[MappingCandidate] = Field(default_factory=list)
    reason: str = ""


class DependencyCheck(BaseModel):
    source: str
    target: str
    reflexion: Literal["convergence", "divergence", "absence"]
    verdict: Literal["allowed", "forbidden", "undetermined", "missing"]
    rule_id: str = ""
    occurrences: int = 0
    evidence: list[EvidenceItem] = Field(default_factory=list)


class DriftMetrics(BaseModel):
    """Chỉ số drift. Xem docs/METRICS.md để biết công thức chi tiết."""

    design_components: int = 0
    actual_components: int = 0
    matched_components: int = 0
    design_dependencies: int = 0
    actual_dependencies: int = 0
    convergences: int = 0
    divergences: int = 0
    absences: int = 0
    component_coverage: float = 0.0       # matched / design_components
    dependency_conformance: float = 0.0   # convergences / actual_dependencies (precision)
    dependency_realization: float = 0.0   # convergences / design_dependencies (recall)
    structural_similarity: float = 0.0    # Jaccard trên (tập node ∪ tập cạnh)
    drift_score: float = 0.0              # 1 - structural_similarity (khoảng cách sửa đồ thị chuẩn hóa)
    violation_index: float = 0.0          # tổng (trọng số mức độ * confidence) / số dependency xét
    drift_level: Literal["none", "low", "moderate", "high", "severe"] = "none"
    previous_drift_score: Optional[float] = None
    drift_trend: Optional[Literal["improving", "stable", "worsening"]] = None
    changed_relations: dict[str, list[str]] = Field(default_factory=dict)


class GraphNodeView(BaseModel):
    id: str
    label: str
    kind: Literal["design", "code", "phantom", "external"]
    status: str = ""  # matched / missing / unexpected / uncertain
    layer: Optional[str] = None


class GraphEdgeView(BaseModel):
    source: str
    target: str
    reflexion: Literal["convergence", "divergence", "absence"]
    verdict: str = ""
    weight: int = 0


class VisualizationData(BaseModel):
    """Đồ thị so sánh (node-link) để Reporting Agent vẽ trực tiếp."""

    nodes: list[GraphNodeView] = Field(default_factory=list)
    edges: list[GraphEdgeView] = Field(default_factory=list)


class AdaptationStep(BaseModel):
    phase: Literal["monitor", "analyze", "plan", "execute", "knowledge"]
    detail: dict[str, Any] = Field(default_factory=dict)


class AppliedStrategy(BaseModel):
    scope: Literal["full", "incremental"] = "full"
    focus_components: list[str] = Field(default_factory=list)
    matching_rounds: int = 3
    deep_matching: bool = False
    confidence_threshold: float = 0.7
    mapping_accept_threshold: float = 0.75
    review_mode: Literal["off", "local", "llm"] = "local"
    review_scope: Literal["low_confidence", "all"] = "all"
    llm_budget: int = 0
    reasons: list[str] = Field(default_factory=list)
    source: Literal["agent", "coordinator", "agent+coordinator"] = "agent"


class ScenarioItem(BaseModel):
    kind: Literal["component", "dependency"]
    source: str
    target: str = ""
    detail: str = ""            # vd "convergence", "orphan", "phantom", "forbidden_dependency"
    finding_ids: list[str] = Field(default_factory=list)


class ScenarioReport(BaseModel):
    """Tổng hợp theo 4 kịch bản. Reporting Agent có thể dùng trực tiếp để trình bày."""

    verdict: Literal["match", "mismatch", "inconclusive"]
    primary_scenario: Scenario
    scenarios_present: list[Scenario] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    match: list[ScenarioItem] = Field(default_factory=list)
    wrong: list[ScenarioItem] = Field(default_factory=list)
    missing: list[ScenarioItem] = Field(default_factory=list)
    junk: list[ScenarioItem] = Field(default_factory=list)
    uncertain: list[ScenarioItem] = Field(default_factory=list)
    match_ratio: float = 0.0     # phần tử khớp / tổng phần tử xét (component + dependency)
    summary: str = ""
    summary_by: str = "template"  # template | local-fewshot | <tên model LLM>


class ReviewMetadata(BaseModel):
    mode: Literal["off", "local", "llm"] = "off"
    provider: str = "none"
    model: str = ""
    policy: Literal["advisory", "resolve"] = "advisory"
    reviewed: int = 0
    llm_calls: int = 0
    cache_hits: int = 0
    fallbacks: int = 0                # số lần gọi LLM thất bại
    local_fallback_reviews: int = 0   # số finding phải dùng bộ cục bộ dù đã cấu hình LLM (lỗi / hết ngân sách)
    status_changes: int = 0
    disagreements: int = 0      # số finding reviewer bác bỏ hoặc xếp kịch bản khác -> ưu tiên người xem lại
    rag_documents: int = 0
    few_shot_pool: int = 0
    errors: list[str] = Field(default_factory=list)


class Statistics(BaseModel):
    total_findings: int = 0
    open_violations: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    by_severity: dict[str, int] = Field(default_factory=dict)
    by_status: dict[str, int] = Field(default_factory=dict)
    valid_dependencies: int = 0
    invalid_dependencies: int = 0
    undetermined_dependencies: int = 0


class PerformanceMetadata(BaseModel):
    total_ms: float = 0.0
    phase_ms: dict[str, float] = Field(default_factory=dict)
    design_nodes: int = 0
    design_edges: int = 0
    code_modules: int = 0
    code_dependencies: int = 0
    internal_dependencies: int = 0
    unresolved_imports: int = 0
    llm_calls: int = 0  # số lần gọi API LLM thật (0 khi chạy local / dùng cache)


class ConsistencyReport(BaseModel):
    schema_version: str = "1.0.0"
    agent_name: str = "Consistency Checking Agent"
    project_id: str
    scan_id: str
    commit_hash: str = ""
    generated_at: str
    status: Literal["SUCCESS", "PARTIAL", "FAILED"]
    strategy: AppliedStrategy
    adaptation_trace: list[AdaptationStep] = Field(default_factory=list)
    mappings: list[ComponentMapping] = Field(default_factory=list)
    dependencies: list[DependencyCheck] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    drift: DriftMetrics = Field(default_factory=DriftMetrics)
    scenario_report: Optional[ScenarioReport] = None
    review: ReviewMetadata = Field(default_factory=ReviewMetadata)
    statistics: Statistics = Field(default_factory=Statistics)
    visualization: VisualizationData = Field(default_factory=VisualizationData)
    performance: PerformanceMetadata = Field(default_factory=PerformanceMetadata)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
