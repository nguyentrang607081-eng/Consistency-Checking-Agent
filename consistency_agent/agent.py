"""Consistency Checking Agent - lớp vỏ (Facade) + workflow MAPE-K.

Workflow nội bộ (LangGraph StateGraph nếu đã cài langgraph, ngược lại chạy tuần tự cùng thứ tự node -
giống cách repo Coordinator / Adaptive Recommendation của nhóm làm):

    normalize -> monitor -> analyze -> plan -> execute -> knowledge -> package
"""
from __future__ import annotations

import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional, TypedDict, Union

from pydantic import ValidationError

from .adapters.architecture_adapter import to_design_model
from .adapters.code_adapter import to_code_model
from . import scenarios
from .adaptive import mape_k
from .adaptive.knowledge import KnowledgeStore
from .config import AgentConfig
from .core.checker import CheckOutput, ConsistencyChecker
from .core.graph_matching import GraphMatcher, MappingResult
from .llm.providers import Reviewer, create_reviewer
from .review import ReviewEngine
from .schemas.inputs import CheckContext, RuleSet, StrategyDirective
from .schemas.internal import CodeModel, DesignModel
from .schemas.outputs import (AdaptationStep, AppliedStrategy, ConsistencyReport, PerformanceMetadata,
                              ReviewMetadata, ScenarioReport, Statistics)

try:  # LangGraph là tùy chọn
    from langgraph.graph import END, START, StateGraph
    HAS_LANGGRAPH = True
except ImportError:  # pragma: no cover - phụ thuộc môi trường
    HAS_LANGGRAPH = False

log = logging.getLogger(__name__)
JsonLike = Union[dict, Any]


class CCAState(TypedDict, total=False):
    raw: dict
    context: CheckContext
    rules: RuleSet
    directive: Optional[StrategyDirective]
    design: DesignModel
    code: CodeModel
    obs: Any
    ana: Any
    strategy: AppliedStrategy
    mappings: dict[str, MappingResult]
    result: CheckOutput
    scenario_report: ScenarioReport
    review_meta: ReviewMetadata
    trace: list[AdaptationStep]
    timings: dict[str, float]
    warnings: list[str]
    errors: list[str]
    report: ConsistencyReport


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConsistencyCheckingAgent:
    def __init__(self, config: Optional[AgentConfig] = None, knowledge: Union[KnowledgeStore, str, None, bool] = None,
                 reviewer: Union[Reviewer, str, None, bool] = None) -> None:
        """``knowledge``: KnowledgeStore, đường dẫn SQLite, ``None`` (dùng config.kb_path) hoặc ``False`` (tắt).
        ``reviewer``: đối tượng Reviewer, tên provider ("local", "groq", "ollama", ...), ``None`` (theo config /
        biến CCA_LLM_PROVIDER) hoặc ``False`` (tắt review)."""
        self.cfg = config or AgentConfig()
        self.reviewer_warning = ""
        if reviewer is False:
            self.reviewer: Optional[Reviewer] = None
        elif reviewer is None or isinstance(reviewer, str):
            try:
                self.reviewer = create_reviewer(reviewer or self.cfg.llm_provider)
            except ValueError as exc:  # thiếu key... -> không chặn agent, dùng bộ cục bộ
                self.reviewer = create_reviewer("local")
                self.reviewer_warning = f"Không khởi tạo được LLM ({exc}); dùng RAG + few-shot cục bộ."
                log.warning(self.reviewer_warning)
        else:
            self.reviewer = reviewer
        if knowledge is False:
            self.kb: Optional[KnowledgeStore] = None
        elif isinstance(knowledge, KnowledgeStore):
            self.kb = knowledge
        elif isinstance(knowledge, str) and knowledge:
            self.kb = KnowledgeStore(knowledge)
        else:  # None hoặc True -> đường dẫn mặc định trong config
            self.kb = KnowledgeStore(self.cfg.kb_path)
        self.graph = self._build_graph() if HAS_LANGGRAPH else None

    # ================================================================ nodes
    def _timed(self, s: CCAState, name: str, t0: float) -> dict[str, float]:
        timings = dict(s.get("timings", {}))
        timings[name] = round((time.perf_counter() - t0) * 1000, 3)
        return timings

    def node_normalize(self, s: CCAState) -> dict:
        t0 = time.perf_counter()
        raw = s["raw"]
        rules = RuleSet.model_validate(raw.get("rules") or {})
        directive = StrategyDirective.model_validate(raw["strategy"]) if raw.get("strategy") else None
        design = to_design_model(raw["architecture_graph"], rules)
        code = to_code_model(raw["code_envelope"], self.cfg.ignore_patterns, self.cfg.source_roots)
        ctx = CheckContext.model_validate(raw.get("context") or {})
        if not ctx.project_id:
            ctx.project_id = code.project_id
        if not ctx.scan_id:
            ctx.scan_id = code.run_id if code.run_id != "unknown-run" else datetime.now().strftime("scan-%Y%m%d-%H%M%S")
        for comp_id, pkgs in rules.component_mapping.items():
            if comp_id not in design.components:
                design.warnings.append(f"component_mapping tham chiếu component không tồn tại: '{comp_id}'.")
        warns = [*design.warnings, *code.warnings] + ([self.reviewer_warning] if self.reviewer_warning else [])
        if not any(c.expected_in_code for c in design.components.values()):
            warns.append("Kiến trúc thiết kế không có component nào để đối chiếu với mã nguồn.")
        if not code.modules:
            warns.append("Mã nguồn không có module Python nào (envelope rỗng hoặc mọi file bị bỏ qua).")
        return {"rules": rules, "directive": directive, "design": design, "code": code, "context": ctx,
                "warnings": warns, "errors": [],
                "timings": self._timed(s, "normalize", t0), "trace": []}

    def node_monitor(self, s: CCAState) -> dict:
        t0 = time.perf_counter()
        obs = mape_k.monitor(s["design"], s["code"], self.kb, s["context"].project_id, s["context"].scan_id)
        return {"obs": obs, "trace": [*s["trace"], AdaptationStep(phase="monitor", detail=obs.to_dict())],
                "timings": self._timed(s, "monitor", t0)}

    def node_analyze(self, s: CCAState) -> dict:
        t0 = time.perf_counter()
        ana = mape_k.analyze(s["obs"], s["design"], s["code"], self.cfg)
        detail = {"size_class": ana.size_class, "design_changed": ana.design_changed,
                  "changed_packages": ana.changed_packages, "change_ratio": ana.change_ratio,
                  "ambiguity_ratio": ana.ambiguity_ratio, "previous_low_confidence_ratio": ana.previous_low_conf_ratio,
                  "data_quality": ana.data_quality, **ana.details}
        return {"ana": ana, "trace": [*s["trace"], AdaptationStep(phase="analyze", detail=detail)],
                "timings": self._timed(s, "analyze", t0)}

    def node_plan(self, s: CCAState) -> dict:
        t0 = time.perf_counter()
        kind = "off" if self.reviewer is None else ("llm" if self.reviewer.is_llm else "local")
        strat = mape_k.plan(s["obs"], s["ana"], s.get("directive"), self.cfg, kind)
        return {"strategy": strat,
                "trace": [*s["trace"], AdaptationStep(phase="plan", detail=strat.model_dump())],
                "timings": self._timed(s, "plan", t0)}

    def _focus_nodes(self, s: CCAState, mappings: dict[str, MappingResult]) -> Optional[set[str]]:
        strat = s["strategy"]
        if strat.scope != "incremental":
            return None
        focus: set[str] = set()
        for f in strat.focus_components:
            f = f.replace("/", ".").removeprefix("code:")
            if f in s["design"].components:
                focus.add(f)
            focus.add(f"code:{f.split('.')[0]}")
            for did, r in mappings.items():
                if any(p == f or p.startswith(f + ".") or f.startswith(p + ".") for p in r.packages):
                    focus.add(did)
        return focus

    def node_execute(self, s: CCAState) -> dict:
        t0 = time.perf_counter()
        strat, ctx, rules = s["strategy"], s["context"], s["rules"]
        design, code = s["design"], s["code"]
        explicit: dict[str, list[str]] = {}
        for cid, comp in design.components.items():
            if comp.code_hints:
                explicit[cid] = comp.code_hints
        explicit.update(rules.component_mapping)
        rejected: set[tuple[str, str]] = set()
        reused: dict[str, tuple[list[str], float, str]] = {}
        decisions: dict[str, dict] = {}
        prev_findings: list[dict] = []
        baseline = s["obs"].baseline
        if self.kb:
            explicit.update({d: p for d, p in self.kb.user_mappings(ctx.project_id).items() if d not in explicit})
            rejected = self.kb.rejected_pairs(ctx.project_id)
            decisions = self.kb.decisions(ctx.project_id)
            if strat.scope == "incremental":
                focus_tops = {f.replace("/", ".").removeprefix("code:").split(".")[0] for f in strat.focus_components}
                for did, (pkgs, conf) in self.kb.auto_mappings(ctx.project_id).items():
                    if did not in strat.focus_components and not any(p.split(".")[0] in focus_tops for p in pkgs):
                        reused[did] = (pkgs, conf, "knowledge:reused")
                if baseline:
                    prev_findings = self.kb.findings_of(ctx.project_id, baseline["scan_id"])

        t_match = time.perf_counter()
        matcher = GraphMatcher(design, code, accept_threshold=strat.mapping_accept_threshold,
                               candidate_threshold=self.cfg.mapping_candidate_threshold, margin=self.cfg.mapping_margin,
                               rounds=strat.matching_rounds, deep=strat.deep_matching, rejected=rejected)
        mappings = matcher.run(explicit, reused)
        timings = self._timed(s, "graph_matching", t_match)

        t_rule = time.perf_counter()
        checker = ConsistencyChecker(design, code, rules, mappings, strat.confidence_threshold)
        result = checker.run(focus=self._focus_nodes(s, mappings), decisions=decisions,
                             previous_findings=prev_findings,
                             previous_drift=baseline["drift_score"] if baseline else None,
                             previous_edges=baseline["actual_edges"] if baseline else None)
        timings["rule_engine_and_drift"] = round((time.perf_counter() - t_rule) * 1000, 3)
        timings["execute"] = round((time.perf_counter() - t0) * 1000, 3)
        detail = {"mapping_status": dict(Counter(r.status for r in mappings.values())),
                  "explicit_mappings": len(explicit), "reused_mappings": len(reused),
                  "rejected_pairs": len(rejected), "rule_engine": checker.engine.describe(),
                  "findings": len(result.findings),
                  "carried_over": sum(1 for f in result.findings if f.carried_over)}
        return {"mappings": mappings, "result": result, "timings": timings,
                "trace": [*s["trace"], AdaptationStep(phase="execute", detail=detail)]}

    def node_review(self, s: CCAState) -> dict:
        """Phân loại 4 kịch bản + rà soát finding bằng RAG + few-shot + LLM (thuộc pha Execute)."""
        t0 = time.perf_counter()
        res, strat = s["result"], s["strategy"]
        scenarios.apply(res.findings)
        meta = ReviewMetadata(mode="off", policy=self.cfg.llm_policy if self.cfg.llm_policy in ("advisory", "resolve")
                              else "advisory")  # type: ignore[arg-type]
        engine = None
        if strat.review_mode != "off" and self.reviewer is not None:
            engine = ReviewEngine(self.reviewer, self.kb, meta.policy, strat.llm_budget)
            meta = engine.run(res.findings, s["design"], s["rules"], s["context"].project_id, strat.review_scope)
        rep = scenarios.build_report(res.findings, res.mappings, res.dependencies)
        if engine is not None:
            engine.summarize(rep, res.findings, meta)
        detail = {"step": "scenario_classification+review", "verdict": rep.verdict,
                  "primary_scenario": rep.primary_scenario, "counts": rep.counts,
                  **meta.model_dump(include={"mode", "provider", "model", "reviewed", "llm_calls", "cache_hits",
                                             "fallbacks", "status_changes", "disagreements"})}
        return {"scenario_report": rep, "review_meta": meta, "timings": self._timed(s, "review", t0),
                "trace": [*s["trace"], AdaptationStep(phase="execute", detail=detail)]}

    def node_knowledge(self, s: CCAState) -> dict:
        t0 = time.perf_counter()
        ctx, res = s["context"], s["result"]
        stats = self._stats(res)
        if not self.kb:
            step = AdaptationStep(phase="knowledge", detail={"persisted": False, "reason": "Knowledge bị tắt"})
            return {"trace": [*s["trace"], step], "timings": self._timed(s, "knowledge", t0)}
        auto = {d: (r.packages, r.confidence, r.status) for d, r in s["mappings"].items()
                if r.status in ("confirmed", "uncertain") and r.method not in ("explicit",)}
        warnings = list(s["warnings"])
        try:
            self.kb.save_scan(ctx.project_id, ctx.scan_id, commit_hash=ctx.commit_hash,
                              drift_score=res.drift.drift_score, design_signature=s["obs"].design_signature,
                              package_signatures=res.package_signatures, actual_edges=res.actual_edges,
                              strategy=s["strategy"].model_dump(), stats=stats,
                              findings=[f.model_dump(mode="json") for f in res.findings], auto_mappings=auto)
            detail = {"persisted": True, "kb_path": self.kb.path, "auto_mappings_saved": len(auto),
                      "findings_saved": len(res.findings)}
        except Exception as exc:  # lỗi DB không làm hỏng kết quả kiểm tra
            log.exception("Không ghi được Knowledge")
            warnings.append(f"Không ghi được Knowledge: {exc}")
            detail = {"persisted": False, "error": str(exc)}
        return {"warnings": warnings, "trace": [*s["trace"], AdaptationStep(phase="knowledge", detail=detail)],
                "timings": self._timed(s, "knowledge", t0)}

    def node_package(self, s: CCAState) -> dict:
        ctx, res, obs = s["context"], s["result"], s["obs"]
        st = self._stats(res)
        stats = Statistics(**{k: v for k, v in st.items() if k in Statistics.model_fields})
        perf = PerformanceMetadata(
            total_ms=round(sum(v for k, v in s["timings"].items() if k in
                               ("normalize", "monitor", "analyze", "plan", "execute", "review", "knowledge")), 3),
            phase_ms=s["timings"], design_nodes=obs.design_components, design_edges=obs.design_edges,
            code_modules=obs.code_modules, code_dependencies=obs.code_dependencies,
            internal_dependencies=obs.internal_dependencies, unresolved_imports=obs.unresolved_imports,
            llm_calls=s["review_meta"].llm_calls)
        status = "SUCCESS" if s["code"].status == "SUCCESS" else "PARTIAL"
        report = ConsistencyReport(
            project_id=ctx.project_id, scan_id=ctx.scan_id, commit_hash=ctx.commit_hash, generated_at=_now(),
            status=status, strategy=s["strategy"], adaptation_trace=s["trace"],
            mappings=res.mappings, dependencies=res.dependencies, findings=res.findings, drift=res.drift,
            scenario_report=s["scenario_report"], review=s["review_meta"],
            statistics=stats, visualization=res.visualization, performance=perf,
            warnings=s["warnings"], errors=s["errors"])
        return {"report": report}

    @staticmethod
    def _stats(res: CheckOutput) -> dict:
        f = res.findings
        open_v = [x for x in f if x.status in ("confirmed", "needs_review") and x.violation_type != "uncertain_mapping"]
        low = [x for x in f if x.status in ("needs_review", "undetermined")]
        return {
            "total_findings": len(f), "open_violations": len(open_v),
            "by_type": dict(Counter(x.violation_type for x in f)),
            "by_severity": dict(Counter(x.severity for x in f)),
            "by_status": dict(Counter(x.status for x in f)),
            "valid_dependencies": sum(1 for d in res.dependencies if d.verdict == "allowed"),
            "invalid_dependencies": sum(1 for d in res.dependencies if d.verdict == "forbidden"),
            "undetermined_dependencies": sum(1 for d in res.dependencies if d.verdict == "undetermined"),
            "low_confidence_ratio": round(len(low) / len(f), 4) if f else 0.0,
            "drift_score": res.drift.drift_score,
        }

    # ================================================================ graph
    NODE_ORDER = ("normalize", "monitor", "analyze", "plan", "execute", "review", "knowledge", "package")

    def _build_graph(self):  # pragma: no cover - chỉ chạy khi có langgraph
        g = StateGraph(CCAState)
        for name in self.NODE_ORDER:
            g.add_node(name, getattr(self, f"node_{name}"))
        g.add_edge(START, self.NODE_ORDER[0])
        for a, b in zip(self.NODE_ORDER, self.NODE_ORDER[1:]):
            g.add_edge(a, b)
        g.add_edge(self.NODE_ORDER[-1], END)
        return g.compile()

    def _invoke(self, raw: dict) -> ConsistencyReport:
        state: CCAState = {"raw": raw}
        if self.graph is not None:
            return self.graph.invoke(state)["report"]
        for name in self.NODE_ORDER:
            state.update(getattr(self, f"node_{name}")(state))  # type: ignore[typeddict-item]
        return state["report"]

    # ================================================================ API
    def check(self, architecture_graph: JsonLike, code_envelope: JsonLike, rules: Optional[JsonLike] = None,
              strategy: Optional[JsonLike] = None, context: Optional[JsonLike] = None) -> ConsistencyReport:
        """API đồng bộ. Đầu vào là dict JSON (hoặc model Pydantic của AEA/SCA)."""
        def as_dict(x: Any) -> Any:
            return x.model_dump() if hasattr(x, "model_dump") else x
        raw = {"architecture_graph": as_dict(architecture_graph), "code_envelope": as_dict(code_envelope),
               "rules": as_dict(rules), "strategy": as_dict(strategy), "context": as_dict(context)}
        try:
            return self._invoke(raw)
        except (ValidationError, KeyError, ValueError, TypeError) as exc:
            log.error("Đầu vào không hợp lệ: %s", exc)
            ctx = context or {}
            return ConsistencyReport(project_id=ctx.get("project_id") or "unknown", scan_id=ctx.get("scan_id") or "unknown",
                                     generated_at=_now(), status="FAILED", strategy=AppliedStrategy(),
                                     errors=[f"Đầu vào không hợp lệ: {exc}"])

    async def run(self, input_data: dict) -> dict:
        """API bất đồng bộ cùng kiểu với các agent khác của nhóm: nhận dict, trả dict JSON."""
        report = self.check(input_data.get("architecture_graph"), input_data.get("code_envelope"),
                            input_data.get("rules"), input_data.get("strategy"), input_data.get("context"))
        return report.model_dump(mode="json")

    # ---------------------------------------------------------------- feedback (human-in-the-loop)
    def _require_kb(self) -> KnowledgeStore:
        if not self.kb:
            raise RuntimeError("Knowledge đang tắt, không thể lưu phản hồi.")
        return self.kb

    def accept_finding(self, project_id: str, fingerprint: str, note: str = "") -> None:
        self._require_kb().record_decision(project_id, fingerprint, "accepted", note)

    def mark_false_positive(self, project_id: str, fingerprint: str, note: str = "") -> None:
        self._require_kb().record_decision(project_id, fingerprint, "false_positive", note)

    def confirm_mapping(self, project_id: str, design_id: str, packages: list[str]) -> None:
        self._require_kb().set_user_mapping(project_id, design_id, packages, "confirmed")

    def reject_mapping(self, project_id: str, design_id: str, package: str) -> None:
        self._require_kb().set_user_mapping(project_id, design_id, [package], "rejected")
