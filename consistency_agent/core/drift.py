"""SIMILARITY & ARCHITECTURE DRIFT.

Ký hiệu (sau khi ánh xạ, hai đồ thị dùng chung không gian node id):
  N_D, E_D : component / dependency của kiến trúc thiết kế (chỉ component kỳ vọng có trong code)
  N_A, E_A : component / dependency của kiến trúc thực tế
  C = E_D ∩ E_A (convergence), E_A \\ E_D (divergence), E_D \\ E_A (absence)  - Reflexion Model

  structural_similarity = (|N_D ∩ N_A| + |E_D ∩ E_A|) / (|N_D ∪ N_A| + |E_D ∪ E_A|)
      = 1 - (số thao tác thêm/xóa node và cạnh cần để biến đồ thị này thành đồ thị kia)
            / (kích thước hợp của hai đồ thị)
      -> chính là khoảng cách sửa đồ thị (graph edit distance) chuẩn hóa khi đã biết ánh xạ node,
         chỉ dùng phép thêm/xóa. Giá trị 1 = giống hệt.
  drift_score            = 1 - structural_similarity
  dependency_conformance = |C| / |E_A|  (bao nhiêu % phụ thuộc thực tế đúng thiết kế)
  dependency_realization = |C| / |E_D|  (bao nhiêu % phụ thuộc thiết kế đã hiện thực)
  violation_index        = Σ(trọng số mức độ × confidence) của vi phạm đang mở / |N ∪| + |E ∪|
      trọng số: low .25, medium .5, high .75, critical 1.0
      (drift đo "khác bao nhiêu", violation_index đo "khác theo hướng xấu, nặng bao nhiêu")

drift_level: <0.05 none, <0.20 low, <0.40 moderate, <0.60 high, còn lại severe.
Ngưỡng này là quy ước ban đầu của CCA, cần hiệu chỉnh bằng thực nghiệm.
"""
from __future__ import annotations

from typing import Optional

from ..schemas.outputs import DriftMetrics
from .confidence import SEVERITY_WEIGHT

OPEN_STATUSES = {"confirmed", "needs_review"}
NON_VIOLATION_TYPES = {"uncertain_mapping"}


def _level(d: float) -> str:
    if d < 0.05:
        return "none"
    if d < 0.20:
        return "low"
    if d < 0.40:
        return "moderate"
    if d < 0.60:
        return "high"
    return "severe"


def compute(design_nodes: set[str], actual_nodes: set[str], design_edges: set[tuple[str, str]],
            actual_edges: set[tuple[str, str]], findings: list, previous: Optional[float] = None,
            previous_edges: Optional[set[tuple[str, str]]] = None) -> DriftMetrics:
    conv = design_edges & actual_edges
    n_union = design_nodes | actual_nodes
    e_union = design_edges | actual_edges
    denom = len(n_union) + len(e_union)
    sim = 1.0 if denom == 0 else (len(design_nodes & actual_nodes) + len(conv)) / denom
    vi_sum = sum(SEVERITY_WEIGHT[f.severity] * f.confidence_score for f in findings
                 if f.status in OPEN_STATUSES and f.violation_type not in NON_VIOLATION_TYPES)
    drift = round(1 - sim, 4)

    def fmt(edges: set[tuple[str, str]]) -> list[str]:
        return sorted(f"{a} -> {b}" for a, b in edges)

    changed = {
        "added_dependencies": fmt(actual_edges - design_edges),
        "removed_dependencies": fmt(design_edges - actual_edges),
        "added_components": sorted(actual_nodes - design_nodes),
        "missing_components": sorted(design_nodes - actual_nodes),
    }
    if previous_edges is not None:
        changed["new_since_last_scan"] = fmt(actual_edges - previous_edges)
        changed["gone_since_last_scan"] = fmt(previous_edges - actual_edges)

    trend = None
    if previous is not None:
        delta = drift - previous
        trend = "worsening" if delta > 0.02 else "improving" if delta < -0.02 else "stable"

    return DriftMetrics(
        design_components=len(design_nodes), actual_components=len(actual_nodes),
        matched_components=len(design_nodes & actual_nodes),
        design_dependencies=len(design_edges), actual_dependencies=len(actual_edges),
        convergences=len(conv), divergences=len(actual_edges - design_edges),
        absences=len(design_edges - actual_edges),
        component_coverage=round(len(design_nodes & actual_nodes) / len(design_nodes), 4) if design_nodes else 1.0,
        dependency_conformance=round(len(conv) / len(actual_edges), 4) if actual_edges else 1.0,
        dependency_realization=round(len(conv) / len(design_edges), 4) if design_edges else 1.0,
        structural_similarity=round(sim, 4), drift_score=drift,
        violation_index=round(vi_sum / denom, 4) if denom else 0.0,
        drift_level=_level(drift), previous_drift_score=previous, drift_trend=trend,  # type: ignore[arg-type]
        changed_relations=changed,
    )
