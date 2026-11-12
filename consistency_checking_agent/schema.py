"""Pydantic data contracts for CCA. Architecture fields mirror schema 1.0.0."""
from typing import Any, Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator

class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow")

class ArchNode(FlexibleModel):
    id: str
    name: str
    type: str = "component"
    c4_level: str = "component"
    properties: dict[str, Any] = Field(default_factory=dict)
    source: str = "diagram"
    confidence: float = Field(default=1.0, ge=0, le=1)
    evidence: str | None = None

class ArchEdge(FlexibleModel):
    id: str | None = None
    source_id: str
    target_id: str
    relationship_type: str = "dependency"
    label: str | None = None
    source: str = "diagram"
    confidence: float = Field(default=1.0, ge=0, le=1)

class ArchitectureGraph(FlexibleModel):
    schema_version: str = "1.0.0"
    system_name: str = "Extracted System"
    nodes: list[ArchNode] = Field(default_factory=list)
    edges: list[ArchEdge] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

class CodeNode(FlexibleModel):
    id: str
    name: str
    type: Literal["module", "class", "function"] | str = "module"
    file_path: str | None = None
    language: str = "python"
    properties: dict[str, Any] = Field(default_factory=dict)

class CodeEdge(FlexibleModel):
    source_id: str
    target_id: str
    relationship_type: str = "import"
    file_path: str | None = None
    line_number: int | None = Field(default=None, ge=1)
    code_snippet: str | None = None

class CodeGraph(FlexibleModel):
    nodes: list[CodeNode] = Field(default_factory=list)
    edges: list[CodeEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_edge_nodes(self):
        ids = {n.id for n in self.nodes}
        missing = [(e.source_id, e.target_id) for e in self.edges
                   if e.source_id not in ids or e.target_id not in ids]
        if missing:
            raise ValueError(f"CodeEdge references unknown node IDs: {missing[:5]}")
        return self

class ArchitectureRules(FlexibleModel):
    layers: list[str] = Field(default_factory=lambda: ["presentation", "business", "data_access", "database"])
    allow_skip: bool = False
    allow_back_call: bool = False
    allowed_edges: list[dict[str, str]] = Field(default_factory=list)
    modules: list[dict[str, Any]] = Field(default_factory=list)
    node_layers: dict[str, str] = Field(default_factory=dict)
    node_modules: dict[str, str] = Field(default_factory=dict)

class HumanDecision(FlexibleModel):
    source_id: str
    target_id: str
    decision: Literal["approve_drift", "reject_drift"]

class ConsistencyInput(FlexibleModel):
    system_name: str = "Unnamed System"
    confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    architecture_graph: ArchitectureGraph
    code_graph: CodeGraph
    rules: ArchitectureRules | dict[str, Any] | None = None
    human_decisions: list[HumanDecision] = Field(default_factory=list)

class MappingEntry(FlexibleModel):
    code_node_id: str
    arch_node_id: str | None = None
    score: float = Field(ge=0, le=1)
    method: Literal["exact", "path_rule", "fuzzy", "llm", "human"] | str
    needs_human_review: bool = False

class ViolationItem(FlexibleModel):
    violation_id: str
    violation_type: Literal["layer_skipping", "back_call", "cross_module", "circular_dependency", "intentional_drift"]
    source_id: str
    target_id: str
    file_path: str | None = None
    line_number: int | None = None
    code_snippet: str | None = None
    severity: Literal["high", "medium", "low"]
    confidence: float = Field(ge=0, le=1)
    human_decision: str | None = None
    evidence_rule: str

class DriftCandidate(FlexibleModel):
    candidate_id: str
    drift_kind: Literal["undeclared_edge", "missing_edge", "unmapped_node"]
    source_id: str
    target_id: str | None = None
    reason: str
    confidence: float = Field(ge=0, le=1)
    needs_human_review: bool = True

class MappingReport(FlexibleModel):
    mapped: list[MappingEntry] = Field(default_factory=list)
    unmapped_code: list[str] = Field(default_factory=list)
    unmatched_architecture: list[str] = Field(default_factory=list)
    mapping_coverage: float = Field(default=0, ge=0, le=1)

class ConsistencySummary(FlexibleModel):
    total_code_edges: int = 0
    mapped_edges: int = 0
    total_violations: int = 0
    by_type: dict[str, int] = Field(default_factory=dict)
    drift_candidate_count: int = 0
    cache_hits: int = 0

class ConsistencyReport(FlexibleModel):
    schema_version: str = "1.0.0"
    system_name: str
    summary: ConsistencySummary
    violations: list[ViolationItem] = Field(default_factory=list)
    drift_candidates: list[DriftCandidate] = Field(default_factory=list)
    mapping_report: MappingReport
    consistency_score: float = Field(default=1, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
