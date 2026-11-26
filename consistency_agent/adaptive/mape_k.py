"""Vòng MAPE-K ở phạm vi Consistency Checking Agent.

  Monitor : đo đặc điểm đầu vào (kích thước graph, chất lượng dữ liệu) + so với lần quét trước (Knowledge).
  Analyze : suy ra kích thước, mức thay đổi, rủi ro ánh xạ mơ hồ, chất lượng dữ liệu.
  Plan    : chọn chiến lược kiểm tra (full/incremental, vùng tập trung, deep matching, ngưỡng tin cậy).
  Execute : agent.py chạy matching + rule engine + drift theo chiến lược.
  Knowledge: lưu ánh xạ, chữ ký package, findings, drift cho lần sau (adaptive/knowledge.py).

Coordinator Agent điều phối ở cấp hệ thống: mọi trường trong ``StrategyDirective`` mà Coordinator
gửi xuống (scope, target_modules, confidence_threshold, deep_matching) được ưu tiên hơn quyết định
cục bộ của CCA; CCA ghi rõ nguồn của từng quyết định trong ``strategy.reasons``.

Các luật Plan (đánh số để trích dẫn trong báo cáo):
  P1  Coordinator chỉ định scope / target_modules / threshold -> tuân theo.
  P2  Chưa có lần quét trước hoặc tài liệu kiến trúc đã đổi -> full.
  P3  Đồ thị nhỏ -> full (rẻ, chính xác nhất).
  P4  Đồ thị lớn, có baseline, tỷ lệ package thay đổi < 30% -> incremental: chỉ xét lại các
      component thay đổi + láng giềng; tái sử dụng ánh xạ & finding còn lại từ Knowledge.
  P5  Tỷ lệ thay đổi >= 30% -> full.
  P6  >30% component có tên mơ hồ, hoặc lần trước >30% finding/ánh xạ tin cậy thấp -> deep matching
      (4 vòng lan truyền cấu trúc + bằng chứng nội dung module).
  P7  Dữ liệu kém (SCA PARTIAL/FAILED hoặc độ tin cậy trung bình của node thiết kế < 0.8)
      -> nâng ngưỡng tin cậy +0.1 (tối đa 0.9): nhiều finding chuyển sang "cần xác nhận".
  P8  Chế độ review (RAG + few-shot + LLM): theo reviewer được cấu hình (off / local / llm);
      Coordinator có thể bật/tắt (llm_review).
  P9  Có LLM thật: đồ thị nhỏ -> rà soát mọi finding; đồ thị lớn -> chỉ finding tin cậy thấp (tiết kiệm chi phí).
  P10 Lần trước >30% kết quả tin cậy thấp hoặc Coordinator yêu cầu kiểm tra lại bằng LLM -> nhân đôi
      ngân sách gọi LLM (tối đa 100).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional

from ..config import AgentConfig
from ..core.checker import package_signatures
from ..core.naming import name_similarity
from ..schemas.inputs import StrategyDirective
from ..schemas.internal import CodeModel, DesignModel
from ..schemas.outputs import AppliedStrategy
from .knowledge import KnowledgeStore


def design_signature(design: DesignModel) -> str:
    payload = {"n": sorted((c.id, c.name, c.type) for c in design.components.values()),
               "e": sorted(design.graph.edges)}
    return hashlib.sha1(json.dumps(payload).encode()).hexdigest()[:16]


@dataclass
class Observation:
    design_components: int
    design_edges: int
    code_modules: int
    code_dependencies: int
    internal_dependencies: int
    unresolved_imports: int
    sca_status: str
    design_avg_confidence: float
    design_signature: str
    package_signatures: dict[str, str]
    has_baseline: bool
    baseline: Optional[dict[str, Any]] = None

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("baseline", "package_signatures")}
        d["baseline_scan_id"] = self.baseline["scan_id"] if self.baseline else None
        d["top_level_packages"] = len(self.package_signatures)
        return d


@dataclass
class Analysis:
    size_class: str
    design_changed: bool
    changed_packages: list[str]
    change_ratio: float
    ambiguity_ratio: float
    previous_low_conf_ratio: float
    data_quality: str
    details: dict = field(default_factory=dict)


def monitor(design: DesignModel, code: CodeModel, kb: Optional[KnowledgeStore], project_id: str,
            scan_id: str) -> Observation:
    confs = [c.confidence for c in design.components.values()]
    baseline = kb.last_scan(project_id, exclude_scan=scan_id) if kb else None
    return Observation(
        design_components=len(design.components), design_edges=design.graph.number_of_edges(),
        code_modules=len(code.modules), code_dependencies=len(code.dependencies),
        internal_dependencies=sum(1 for d in code.dependencies if d.resolution == "internal"),
        unresolved_imports=sum(1 for d in code.dependencies if d.resolution == "unresolved_internal"),
        sca_status=code.status, design_avg_confidence=round(sum(confs) / len(confs), 4) if confs else 1.0,
        design_signature=design_signature(design), package_signatures=package_signatures(code),
        has_baseline=baseline is not None, baseline=baseline)


def analyze(obs: Observation, design: DesignModel, code: CodeModel, cfg: AgentConfig) -> Analysis:
    large = obs.code_modules > cfg.large_code_modules or obs.design_components > cfg.large_design_components
    changed: list[str] = []
    design_changed = False
    prev_low = 0.0
    if obs.baseline:
        prev = obs.baseline["package_signatures"]
        keys = set(prev) | set(obs.package_signatures)
        changed = sorted(k for k in keys if prev.get(k) != obs.package_signatures.get(k))
        design_changed = obs.baseline["design_signature"] != obs.design_signature
        prev_low = float(obs.baseline.get("stats", {}).get("low_confidence_ratio", 0.0))
    ratio = len(changed) / max(1, len(obs.package_signatures))

    # Rủi ro mơ hồ: component có điểm tên tốt nhất < 0.75 với mọi package cấp cao (ước lượng rẻ, chỉ tên)
    tops = sorted({m.split(".")[0] for m in code.modules})
    expected = [c for c in design.components.values() if c.expected_in_code]
    ambiguous = 0
    for c in expected:
        best = max((name_similarity([c.name, c.id], p)[0] for p in tops), default=0.0)
        if best < 0.75:
            ambiguous += 1
    amb_ratio = ambiguous / len(expected) if expected else 0.0

    quality = "good"
    if obs.sca_status != "SUCCESS" or obs.design_avg_confidence < 0.8:
        quality = "degraded"
    return Analysis(size_class="large" if large else "small", design_changed=design_changed,
                    changed_packages=changed, change_ratio=round(ratio, 4), ambiguity_ratio=round(amb_ratio, 4),
                    previous_low_conf_ratio=prev_low, data_quality=quality,
                    details={"ambiguous_components": ambiguous, "expected_components": len(expected)})


def plan(obs: Observation, ana: Analysis, directive: Optional[StrategyDirective], cfg: AgentConfig,
         reviewer_kind: str = "local") -> AppliedStrategy:
    reasons: list[str] = []
    from_coord = False
    directive = directive or StrategyDirective()

    threshold = cfg.confidence_threshold
    if directive.confidence_threshold is not None:
        threshold = directive.confidence_threshold
        from_coord = True
        reasons.append(f"P1: ngưỡng tin cậy {threshold} do Coordinator chỉ định.")
    if ana.data_quality == "degraded":
        threshold = min(0.9, round(threshold + 0.1, 2))
        reasons.append(f"P7: dữ liệu đầu vào kém (SCA={obs.sca_status}, độ tin cậy thiết kế TB="
                       f"{obs.design_avg_confidence}) -> nâng ngưỡng lên {threshold}.")

    # scope
    focus: list[str] = []
    if directive.scope in ("full", "incremental"):
        scope = directive.scope
        from_coord = True
        reasons.append(f"P1: scope '{scope}' do Coordinator chỉ định.")
        if scope == "incremental":
            focus = list(directive.target_modules) or ana.changed_packages
            if not obs.has_baseline:
                reasons.append("Cảnh báo: chưa có baseline trong Knowledge, quét incremental chỉ xét vùng tập trung.")
    elif directive.target_modules:
        scope, focus = "incremental", list(directive.target_modules)
        from_coord = True
        reasons.append(f"P1: Coordinator yêu cầu quét lại các module {focus}.")
    elif not obs.has_baseline:
        scope = "full"
        reasons.append("P2: chưa có lần quét trước trong Knowledge -> quét toàn bộ.")
    elif ana.design_changed:
        scope = "full"
        reasons.append("P2: tài liệu kiến trúc đã thay đổi so với lần trước -> quét toàn bộ.")
    elif ana.size_class == "small":
        scope = "full"
        reasons.append(f"P3: đồ thị nhỏ ({obs.code_modules} module, {obs.design_components} component) -> quét toàn bộ.")
    elif ana.change_ratio < cfg.incremental_max_change_ratio:
        scope, focus = "incremental", ana.changed_packages
        reasons.append(f"P4: đồ thị lớn, chỉ {ana.change_ratio:.0%} package thay đổi -> incremental trên {focus}.")
    else:
        scope = "full"
        reasons.append(f"P5: {ana.change_ratio:.0%} package thay đổi (>= {cfg.incremental_max_change_ratio:.0%}) -> quét toàn bộ.")

    deep = False
    if directive.deep_matching is not None:
        deep = bool(directive.deep_matching)
        from_coord = True
        reasons.append(f"P1: deep_matching={deep} do Coordinator chỉ định.")
    elif ana.ambiguity_ratio > cfg.ambiguity_deep_ratio:
        deep = True
        reasons.append(f"P6: {ana.ambiguity_ratio:.0%} component có tên không khớp rõ package -> bật deep matching.")
    elif ana.previous_low_conf_ratio > cfg.low_conf_deep_ratio:
        deep = True
        reasons.append(f"P6: lần quét trước {ana.previous_low_conf_ratio:.0%} kết quả tin cậy thấp -> bật deep matching.")

    # review (RAG + few-shot + LLM)
    review_mode, review_scope, budget = reviewer_kind, "all", 0
    if directive.llm_review is False:
        review_mode = "off"
        from_coord = True
        reasons.append("P8: Coordinator tắt bước review.")
    elif reviewer_kind == "off":
        reasons.append("P8: không cấu hình reviewer (CCA_LLM_PROVIDER=none) -> bỏ qua review.")
    elif reviewer_kind == "local":
        reasons.append("P8: chưa cấu hình LLM -> review bằng RAG + few-shot cục bộ (offline)."
                       + (" Coordinator đề nghị kiểm tra lại bằng LLM: đặt CCA_LLM_PROVIDER (groq/ollama/...)."
                          if directive.llm_review else ""))
    else:
        budget = cfg.llm_max_calls
        if ana.size_class == "large":
            review_scope = "low_confidence"
            reasons.append("P9: đồ thị lớn -> LLM chỉ rà soát finding tin cậy thấp.")
        else:
            reasons.append("P9: đồ thị nhỏ -> LLM rà soát mọi finding (trong ngân sách).")
        if directive.llm_review or ana.previous_low_conf_ratio > cfg.low_conf_deep_ratio:
            budget = min(100, budget * 2)
            from_coord = from_coord or bool(directive.llm_review)
            reasons.append(f"P10: cần kiểm tra lại nhiều kết quả tin cậy thấp -> ngân sách LLM {budget} lượt.")

    source = "agent+coordinator" if from_coord and len(reasons) > 1 else ("coordinator" if from_coord else "agent")
    return AppliedStrategy(scope=scope, focus_components=focus, matching_rounds=4 if deep else 3,  # type: ignore
                           deep_matching=deep, confidence_threshold=threshold,
                           mapping_accept_threshold=cfg.mapping_accept_threshold,
                           review_mode=review_mode, review_scope=review_scope, llm_budget=budget,  # type: ignore
                           reasons=reasons, source=source)  # type: ignore[arg-type]
