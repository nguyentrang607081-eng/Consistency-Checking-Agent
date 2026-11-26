"""Kiểm thử cơ chế Adaptive (MAPE-K) và Knowledge / human-in-the-loop."""
from __future__ import annotations

from consistency_agent.adaptive.knowledge import KnowledgeStore
from consistency_agent.agent import ConsistencyCheckingAgent
from consistency_agent.config import AgentConfig
from tests.helpers import by_type, code_envelope, design_graph, mapping, rules

NODES = [("api", "API"), ("services", "Services"), ("models", "Models"), ("storage", "Storage")]
EDGES = [("api", "services"), ("services", "models"), ("services", "storage"), ("storage", "models")]
CODE = {"api.routes": ["services.orders"], "services.orders": ["models.order", "storage.db"],
        "storage.db": ["models.order"], "models.order": []}


def phases(report) -> list[str]:
    return [s.phase for s in report.adaptation_trace]


def test_mape_k_trace_and_knowledge_between_scans(tmp_path):
    kb = str(tmp_path / "kb.sqlite")
    r1 = ConsistencyCheckingAgent(knowledge=kb).check(design_graph(NODES, EDGES), code_envelope(CODE),
                                                      context={"project_id": "p", "scan_id": "s1"})
    assert phases(r1) == ["monitor", "analyze", "plan", "execute", "execute", "knowledge"]  # execute: kiểm tra + review
    assert any(x.startswith("P2") for x in r1.strategy.reasons)  # chưa có baseline
    assert r1.adaptation_trace[-1].detail["persisted"] is True

    drifted = dict(CODE, **{"models.order": ["api.routes"]})
    # agent MỚI, cùng file Knowledge -> phải nhớ lần quét trước
    r2 = ConsistencyCheckingAgent(knowledge=kb).check(design_graph(NODES, EDGES), code_envelope(drifted),
                                                      context={"project_id": "p", "scan_id": "s2"})
    assert r2.adaptation_trace[0].detail["baseline_scan_id"] == "s1"
    assert any(x.startswith("P3") for x in r2.strategy.reasons)  # đồ thị nhỏ -> full
    assert r2.drift.previous_drift_score == r1.drift.drift_score
    assert r2.drift.drift_trend == "worsening"
    assert "models -> api" in r2.drift.changed_relations["new_since_last_scan"]
    analyze = r2.adaptation_trace[1].detail
    assert analyze["changed_packages"] == ["models"]


def test_large_graph_uses_incremental_scan_and_reuses_knowledge():
    cfg = AgentConfig(large_code_modules=3)  # coi là "lớn" để kích hoạt P4
    agent = ConsistencyCheckingAgent(cfg, knowledge=KnowledgeStore(":memory:"))
    r_rules = rules(default_policy="design_only")
    base_code = dict(CODE, **{"api.routes": ["services.orders", "models.order"]})  # vi phạm cũ ở api
    r1 = agent.check(design_graph(NODES, EDGES), code_envelope(base_code), r_rules, context={"project_id": "p", "scan_id": "s1"})
    assert r1.strategy.scope == "full"
    old = [f for f in r1.findings if f.source_component == "api" and f.target_component == "models"]
    assert old

    changed = dict(base_code, **{"storage.db": ["models.order", "api.routes"]})  # chỉ storage đổi
    r2 = agent.check(design_graph(NODES, EDGES), code_envelope(changed), r_rules, context={"project_id": "p", "scan_id": "s2"})
    assert r2.strategy.scope == "incremental"
    assert r2.strategy.focus_components == ["storage"]
    assert any(x.startswith("P4") for x in r2.strategy.reasons)
    execute = next(s for s in r2.adaptation_trace if s.phase == "execute").detail
    assert execute["reused_mappings"] >= 1
    new = [f for f in r2.findings if f.source_component == "storage" and f.target_component == "api"]
    assert new and not new[0].carried_over
    carried = [f for f in r2.findings if f.source_component == "api" and f.target_component == "models"]
    assert carried and carried[0].carried_over  # kết quả vùng không đổi lấy từ Knowledge


def test_coordinator_directive_overrides_local_plan():
    agent = ConsistencyCheckingAgent(knowledge=False)
    r = agent.check(design_graph(NODES, EDGES), code_envelope(CODE),
                    strategy={"confidence_threshold": 0.9, "target_modules": ["storage"]})
    assert r.strategy.confidence_threshold == 0.9
    assert r.strategy.scope == "incremental" and r.strategy.focus_components == ["storage"]
    assert r.strategy.source in ("coordinator", "agent+coordinator")


def test_degraded_input_raises_threshold_and_marks_partial():
    agent = ConsistencyCheckingAgent(knowledge=False)
    r = agent.check(design_graph(NODES, EDGES), code_envelope(CODE, status="PARTIAL"))
    assert r.status == "PARTIAL"
    assert r.strategy.confidence_threshold == 0.8
    assert any(x.startswith("P7") for x in r.strategy.reasons)


def test_ambiguous_names_enable_deep_matching():
    nodes = [("a", "Alpha Module"), ("b", "Beta Engine"), ("c", "Gamma Store")]
    r = ConsistencyCheckingAgent(knowledge=False).check(
        design_graph(nodes, [("a", "b"), ("b", "c")]),
        code_envelope({"x1.m": ["x2.m"], "x2.m": ["x3.m"], "x3.m": []}))
    assert r.strategy.deep_matching is True and r.strategy.matching_rounds == 4
    assert any(x.startswith("P6") for x in r.strategy.reasons)


def test_accepted_finding_is_remembered():
    kb = KnowledgeStore(":memory:")
    agent = ConsistencyCheckingAgent(knowledge=kb)
    code = dict(CODE, **{"api.routes": ["services.orders", "storage.db"]})
    r_rules = rules({"rule_id": "R1", "kind": "forbid", "source": "api", "target": "storage"})
    r1 = agent.check(design_graph(NODES, EDGES), code_envelope(code), r_rules, context={"project_id": "p", "scan_id": "s1"})
    f = by_type(r1, "forbidden_dependency")[0]
    assert f.status == "confirmed" and r1.statistics.open_violations == 1
    agent.accept_finding("p", f.fingerprint, "Tạm chấp nhận đến sprint sau")
    r2 = agent.check(design_graph(NODES, EDGES), code_envelope(code), r_rules, context={"project_id": "p", "scan_id": "s2"})
    f2 = by_type(r2, "forbidden_dependency")[0]
    assert f2.fingerprint == f.fingerprint and f2.status == "accepted"
    assert "Tạm chấp nhận" in f2.description
    assert r2.statistics.open_violations == 0


def test_user_mapping_feedback_confirm_and_reject():
    kb = KnowledgeStore(":memory:")
    agent = ConsistencyCheckingAgent(knowledge=kb)
    design = design_graph([("payment", "Payment Processor"), ("catalog", "Catalog")], [])
    code = code_envelope({"paymnt_proc.gateway": [], "catalog.items": [], "pay_utils.fmt": []})
    r1 = agent.check(design, code, context={"project_id": "p", "scan_id": "s1"})
    assert mapping(r1, "payment").status == "uncertain"

    agent.reject_mapping("p", "payment", mapping(r1, "payment").code_packages[0])
    r2 = agent.check(design, code, context={"project_id": "p", "scan_id": "s2"})
    assert mapping(r2, "payment").code_packages != mapping(r1, "payment").code_packages

    agent.confirm_mapping("p", "payment", ["paymnt_proc"])
    r3 = agent.check(design, code, context={"project_id": "p", "scan_id": "s3"})
    m = mapping(r3, "payment")
    assert m.status == "confirmed" and m.method == "explicit" and m.confidence == 1.0
    assert not by_type(r3, "uncertain_mapping") or all(f.source_component != "payment" for f in by_type(r3, "uncertain_mapping"))
