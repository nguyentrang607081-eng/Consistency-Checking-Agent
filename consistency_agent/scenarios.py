"""Phân loại kết quả đối chiếu thành 4 KỊCH BẢN.

  1. match   - Cặp dữ liệu khớp hoàn toàn: component ánh xạ chắc chắn, phụ thuộc đúng thiết kế (convergence).
  2. wrong   - Code SAI so với thiết kế: phụ thuộc bị cấm, ngược layer, ngược chiều thiết kế, chu trình,
               phụ thuộc giữa hai component thiết kế mà thiết kế không có.
  3. missing - Code THIẾU so với thiết kế: component hoặc phụ thuộc thiết kế không có trong code.
  4. junk    - Code RÁC: có trong code nhưng thiết kế không có.
                 * orphan       : package không component thiết kế nào dùng tới -> ứng viên code chết
                 * undocumented : package được component thiết kế dùng nhưng tài liệu không mô tả
                 * phantom      : import tới package không tồn tại (sẽ lỗi ImportError)
  (+) uncertain - chưa đủ bằng chứng để xếp loại (ánh xạ chưa chắc chắn / luật không quyết định).

Quy tắc xếp loại là tất định (deterministic). RAG + few-shot + LLM (review.py) chỉ đưa ý kiến thứ hai
cho từng finding, không thay thế quy tắc này.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .schemas.outputs import (ComponentMapping, DependencyCheck, Finding, ScenarioItem, ScenarioReport)

WRONG_TYPES = {"forbidden_dependency", "layer_violation", "reverse_dependency", "cyclic_dependency"}
MISSING_TYPES = {"missing_component", "missing_dependency"}
SEVERITY_WEIGHT = {"low": 1, "medium": 2, "high": 3, "critical": 4}
LABEL_VI = {"match": "khớp hoàn toàn", "wrong": "code sai so với thiết kế", "missing": "code thiếu so với thiết kế",
            "junk": "code rác (thiết kế không có)", "uncertain": "chưa đủ bằng chứng"}


def classify_finding(f: Finding) -> tuple[str, str]:
    """Trả về (scenario, detail) cho một finding."""
    vt = f.violation_type
    if vt == "uncertain_mapping":
        return "uncertain", "mapping"
    if f.status == "undetermined":
        return "uncertain", "rule_unspecified"
    if vt in MISSING_TYPES:
        return "missing", "component" if vt == "missing_component" else "dependency"
    if vt == "unexpected_component":
        return "junk", f.scenario_detail or "undocumented"
    if vt == "unexpected_dependency":
        if f.scenario_detail in ("to_junk", "dependency_to_junk"):
            return "junk", "dependency_to_junk"
        return "wrong", vt
    if vt in WRONG_TYPES:
        return "wrong", vt
    return "uncertain", vt


def apply(findings: Iterable[Finding]) -> None:
    for f in findings:
        f.scenario, detail = classify_finding(f)  # type: ignore[assignment]
        if f.violation_type != "unexpected_component" or not f.scenario_detail:
            f.scenario_detail = detail


def build_report(findings: list[Finding], mappings: list[ComponentMapping],
                 dependencies: list[DependencyCheck]) -> ScenarioReport:
    buckets: dict[str, list[ScenarioItem]] = defaultdict(list)
    for m in mappings:
        if m.status == "confirmed":
            buckets["match"].append(ScenarioItem(kind="component", source=m.design_id,
                                                 target=", ".join(m.code_packages), detail=m.method))
    for d in dependencies:
        if d.reflexion == "convergence" and d.verdict == "allowed":
            buckets["match"].append(ScenarioItem(kind="dependency", source=d.source, target=d.target,
                                                 detail="convergence"))

    grouped: dict[tuple[str, str, str, str], ScenarioItem] = {}
    weight: dict[str, int] = defaultdict(int)
    for f in findings:
        if f.status == "accepted" or f.scenario is None:
            continue
        kind = "component" if f.violation_type in ("missing_component", "unexpected_component",
                                                   "uncertain_mapping") else "dependency"
        key = (f.scenario, kind, f.source_component, f.target_component if kind == "dependency" else "")
        item = grouped.get(key)
        if item is None:
            item = ScenarioItem(kind=kind, source=f.source_component.removeprefix("code:"),
                                target=(f.target_component if kind == "dependency" else "").removeprefix("code:"),
                                detail=f.scenario_detail or f.violation_type)
            grouped[key] = item
            buckets[f.scenario].append(item)
        item.finding_ids.append(f.finding_id)
        weight[f.scenario] += SEVERITY_WEIGHT[f.severity]

    counts = {k: len(buckets.get(k, [])) for k in ("match", "wrong", "missing", "junk", "uncertain")}
    bad = [s for s in ("wrong", "missing", "junk") if counts[s]]
    if sum(counts.values()) == 0:
        verdict, primary = "inconclusive", "uncertain"  # không có gì để đối chiếu -> không được kết luận "khớp"
    elif not bad and not counts["uncertain"]:
        verdict, primary = "match", "match"
    elif not bad:
        verdict, primary = "inconclusive", "uncertain"
    else:
        verdict = "mismatch"
        order = {"wrong": 0, "missing": 1, "junk": 2}
        primary = max(bad, key=lambda s: (weight[s], -order[s]))
    total = sum(counts.values())
    present = [s for s in ("match", "wrong", "missing", "junk", "uncertain") if counts[s]]
    rep = ScenarioReport(verdict=verdict, primary_scenario=primary, scenarios_present=present,  # type: ignore
                         counts=counts, match=buckets["match"], wrong=buckets["wrong"],
                         missing=buckets["missing"], junk=buckets["junk"], uncertain=buckets["uncertain"],
                         match_ratio=round(counts["match"] / total, 4) if total else 0.0)
    rep.summary = template_summary(rep)
    return rep


def _fmt(items: list[ScenarioItem], n: int = 3) -> str:
    parts = [f"{i.source} -> {i.target}" if i.target and i.kind == "dependency" else
             f"{i.source}" + (f" ({i.detail})" if i.detail else "") for i in items[:n]]
    more = f" và {len(items) - n} mục khác" if len(items) > n else ""
    return ", ".join(parts) + more


def template_summary(rep: ScenarioReport) -> str:
    c = rep.counts
    if sum(c.values()) == 0:
        return "Không có component hay phụ thuộc nào để đối chiếu (thiết kế hoặc mã nguồn rỗng) - chưa thể kết luận."
    if rep.verdict == "match":
        return (f"Kịch bản 1 - khớp hoàn toàn: {c['match']} phần tử (component + phụ thuộc) của thiết kế "
                f"đều có trong code, không có phụ thuộc sai, thiếu hay code ngoài kiến trúc.")
    lines = [f"Kết luận: {'KHÔNG khớp' if rep.verdict == 'mismatch' else 'chưa đủ bằng chứng'}; "
             f"kịch bản chính: {LABEL_VI[rep.primary_scenario]}. Tỷ lệ khớp {rep.match_ratio:.0%} "
             f"({c['match']} phần tử khớp)."]
    if rep.wrong:
        lines.append(f"Code sai so với thiết kế ({c['wrong']}): {_fmt(rep.wrong)}.")
    if rep.missing:
        lines.append(f"Code thiếu so với thiết kế ({c['missing']}): {_fmt(rep.missing)}.")
    if rep.junk:
        lines.append(f"Code rác/ngoài kiến trúc ({c['junk']}): {_fmt(rep.junk)}.")
    if rep.uncertain:
        lines.append(f"Cần xác nhận ({c['uncertain']}): {_fmt(rep.uncertain)}.")
    return " ".join(lines)
