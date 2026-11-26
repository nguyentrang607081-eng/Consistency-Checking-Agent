"""Tích hợp với Adaptive Recommendation Agent (repo huainguyn/Adaptive-Recommendation-Agent).

Repo đó nhận ``RecommendationRequest`` (Consistency Result JSON) với ``issues`` mang trạng thái
COMPLIANT / VIOLATION / REVIEW. Hàm dưới đây chuyển ConsistencyReport sang đúng định dạng đó
(schema dùng ``extra="forbid"`` nên chỉ đưa các trường được khai báo).

  confirmed     -> VIOLATION
  needs_review  -> REVIEW
  undetermined  -> REVIEW (rule_status NOT_SPECIFIED)
  accepted      -> bỏ qua
"""
from __future__ import annotations

from typing import Any

from ..schemas.outputs import ConsistencyReport

SEV = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH", "critical": "CRITICAL"}


def to_recommendation_request(report: ConsistencyReport | dict, layer_of: dict[str, str] | None = None,
                              project_name: str | None = None) -> dict[str, Any]:
    if isinstance(report, dict):
        report = ConsistencyReport.model_validate(report)
    layer_of = layer_of or {n.label if n.kind != "design" else n.id: n.layer for n in report.visualization.nodes
                            if n.layer}
    issues, rules = [], {}
    for f in report.findings:
        if f.status == "accepted":
            continue
        status = "VIOLATION" if f.status == "confirmed" else "REVIEW"
        rule_status = "NOT_SPECIFIED" if f.status == "undetermined" or f.rule_id.startswith("DEFAULT") else (
            "FORBIDDEN" if f.violation_type in ("forbidden_dependency", "layer_violation", "reverse_dependency",
                                                "cyclic_dependency") else "NOT_SPECIFIED")
        issues.append({
            "issue_id": f.finding_id,
            "status": status,
            "source_module": f.source_component or "-",
            "target_module": f.target_component or "-",
            "source_layer": layer_of.get(f.source_component),
            "target_layer": layer_of.get(f.target_component),
            "dependency_type": f.violation_type,
            "rule_id": f.rule_id if rule_status != "NOT_SPECIFIED" else None,
            "rule_description": f.description if rule_status != "NOT_SPECIFIED" else None,
            "rule_status": rule_status,
            "confidence": f.confidence_score,
            "severity": SEV[f.severity],
            "evidence": [{"file_path": e.file_path or None, "line_start": e.line_start or None,
                          "line_end": e.line_end or None, "description": e.statement} for e in f.evidence],
            "unresolved_items": [] if status == "VIOLATION" else [f"Xác nhận finding {f.fingerprint}"],
        })
        if rule_status == "FORBIDDEN" and f.rule_id not in rules:
            rules[f.rule_id] = {"rule_id": f.rule_id, "source_layer": layer_of.get(f.source_component),
                                "target_layer": layer_of.get(f.target_component), "status": "FORBIDDEN",
                                "description": f.description}
    return {
        "schema_version": "1.0.0",
        "project_id": report.project_id,
        "run_id": report.scan_id,
        "project_name": project_name or report.project_id,
        "confidence_threshold": report.strategy.confidence_threshold,
        "architecture_rules": list(rules.values()),
        "issues": issues,
    }
