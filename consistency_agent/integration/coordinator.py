"""Tích hợp với Coordinator Agent (repo tranganhne/coordinator-agent).

* ``to_coordinator_input(report)``: ConsistencyReport -> ``CoordinatorInput`` (dict) đúng schema
  ``coordinator_agent/schema.py``: ``{"context": ScanContext, "consistency_result": ConsistencyResult}``.
  Coordinator chỉ chấp nhận 4 loại finding và 3 nguồn, nên CCA ánh xạ:

    forbidden_dependency, layer_violation, reverse_dependency, cyclic_dependency -> "forbidden_dependency"
    unexpected_dependency, unexpected_component                                  -> "divergence"
    missing_dependency                                                           -> "missing_dependency"
    missing_component                                                            -> "absence"
    uncertain_mapping                                                            -> (không gửi; nằm trong warnings)

  Finding đã được người dùng chấp nhận (status "accepted") không gửi sang Coordinator.
  Loại gốc của CCA được giữ trong chuỗi ``evidence`` để không mất thông tin.

* ``directive_from_coordinator(payload)``: nhận ``downstream_payloads["consistency_checking"]`` mà
  Coordinator tạo (``{"confidence_threshold", "target_modules"}``) -> StrategyDirective.
"""
from __future__ import annotations

from typing import Any, Optional

from ..schemas.inputs import StrategyDirective
from ..schemas.outputs import ConsistencyReport

TYPE_MAP = {
    "forbidden_dependency": "forbidden_dependency",
    "layer_violation": "forbidden_dependency",
    "reverse_dependency": "forbidden_dependency",
    "cyclic_dependency": "forbidden_dependency",
    "unexpected_dependency": "divergence",
    "unexpected_component": "divergence",
    "missing_dependency": "missing_dependency",
    "missing_component": "absence",
}


def to_coordinator_input(report: ConsistencyReport | dict, baseline_scan_id: Optional[str] = None) -> dict[str, Any]:
    if isinstance(report, dict):
        report = ConsistencyReport.model_validate(report)
    findings = []
    skipped_uncertain = 0
    for f in report.findings:
        if f.status == "accepted":
            continue
        if f.violation_type not in TYPE_MAP:
            skipped_uncertain += 1
            continue
        ev = "; ".join(e.statement or e.file_path for e in f.evidence[:3])
        findings.append({
            "id": f.finding_id,
            "rule_id": f.rule_id,
            "type": TYPE_MAP[f.violation_type],
            "severity": f.severity,
            "source_component": f.source_component or "-",
            "target_component": f.target_component or "-",
            "file": f.file_path,
            "line": f.line_number,
            "evidence": f"[{f.violation_type}|{f.scenario or '-'}|{f.status}] {f.description} | {ev}".strip(" |"),
            "confidence": f.confidence_score,
            "source": f.detected_by,
        })
    warnings = list(report.warnings)
    if skipped_uncertain:
        warnings.append(f"CCA: {skipped_uncertain} ánh xạ component chưa chắc chắn cần người dùng xác nhận.")
    return {
        "context": {
            "project_id": report.project_id,
            "scan_id": report.scan_id,
            "commit_hash": report.commit_hash,
            "scan_scope": report.strategy.scope,
            "scanned_modules": list(report.strategy.focus_components),
            "baseline_scan_id": baseline_scan_id,
        },
        "consistency_result": {
            "drift_score": report.drift.drift_score,
            "findings": findings,
            "warnings": warnings,
            "errors": list(report.errors),
        },
    }


def directive_from_coordinator(payload: Optional[dict], notes: Optional[list[str]] = None
                               ) -> Optional[StrategyDirective]:
    """Chuyển gợi ý chiến lược của Coordinator thành StrategyDirective (None nếu không có).

    ``notes``: ``strategy_proposal.notes`` của Coordinator. Luật R3 của Coordinator ghi
    "nên bật kiểm tra lại bằng LLM" khi nhiều finding tin cậy thấp -> CCA bật llm_review.
    """
    if not payload and not notes:
        return None
    directive = StrategyDirective.model_validate(payload or {})
    if notes and any("LLM" in n for n in notes):
        directive.llm_review = True
    return directive
