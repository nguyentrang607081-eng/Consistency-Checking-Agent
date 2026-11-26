"""6 kịch bản kiểm thử bắt buộc (PHẦN 6 của yêu cầu)."""
from __future__ import annotations

import pytest

from consistency_agent.agent import ConsistencyCheckingAgent
from tests.helpers import by_type, code_envelope, design_graph, mapping, rules

DESIGN_NODES = [("api", "API"), ("services", "Services"), ("models", "Models"), ("storage", "Storage")]
DESIGN_EDGES = [("api", "services"), ("services", "models"), ("services", "storage"), ("storage", "models")]
CONSISTENT_CODE = {
    "api.routes": ["services.orders", "fastapi"],
    "services.orders": ["models.order", "storage.db"],
    "storage.db": ["models.order", "sqlite3"],
    "models.order": ["dataclasses"],
}


@pytest.fixture
def agent() -> ConsistencyCheckingAgent:
    return ConsistencyCheckingAgent(knowledge=False)


# ---------------------------------------------------------------- Test 1
def test_1_fully_consistent(agent):
    report = agent.check(design_graph(DESIGN_NODES, DESIGN_EDGES), code_envelope(CONSISTENT_CODE))
    assert report.status == "SUCCESS"
    assert all(m.status == "confirmed" for m in report.mappings)
    serious = [f for f in report.findings if f.severity in ("high", "critical")]
    assert serious == []
    assert report.statistics.open_violations == 0
    assert report.drift.drift_score == 0.0
    assert report.drift.structural_similarity == 1.0
    assert report.drift.convergences == 4 and report.drift.divergences == 0 and report.drift.absences == 0
    assert report.drift.drift_level == "none"


# ---------------------------------------------------------------- Test 2
def test_2_forbidden_dependency_with_evidence(agent):
    code = dict(CONSISTENT_CODE)
    code["api.routes"] = ["services.orders", "storage.db"]  # API gọi thẳng Storage
    r = rules({"rule_id": "R-API-DB", "kind": "forbid", "source": "api", "target": "storage", "severity": "high",
               "description": "API không được truy cập Storage trực tiếp"})
    report = agent.check(design_graph(DESIGN_NODES, DESIGN_EDGES), code_envelope(code), r)
    found = by_type(report, "forbidden_dependency")
    assert len(found) == 1
    f = found[0]
    assert (f.source_component, f.target_component) == ("api", "storage")
    assert f.rule_id == "R-API-DB" and f.severity == "high" and f.status == "confirmed"
    assert f.confidence_score >= 0.7
    assert f.file_path.endswith("api/routes.py") and f.line_number == 2
    assert f.evidence and "storage.db" in f.evidence[0].statement
    assert f.recommended_action
    dep = next(d for d in report.dependencies if d.source == "api" and d.target == "storage")
    assert dep.verdict == "forbidden" and dep.reflexion == "divergence"


# ---------------------------------------------------------------- Test 3
def test_3_missing_component(agent):
    nodes = DESIGN_NODES + [("payments", "Payments")]
    edges = DESIGN_EDGES + [("services", "payments")]
    report = agent.check(design_graph(nodes, edges), code_envelope(CONSISTENT_CODE))
    missing = by_type(report, "missing_component")
    assert [f.source_component for f in missing] == ["payments"]
    assert missing[0].severity == "high"  # component có quan hệ trong thiết kế
    assert mapping(report, "payments").status == "unmapped"
    assert "payments" in report.drift.changed_relations["missing_components"]
    # thiếu component không bị báo trùng thành missing_dependency
    assert not [f for f in by_type(report, "missing_dependency") if f.target_component == "payments"]


# ---------------------------------------------------------------- Test 4
def test_4_unexpected_component(agent):
    code = dict(CONSISTENT_CODE)
    code["legacy.export"] = ["storage.db", "csv"]
    report = agent.check(design_graph(DESIGN_NODES, DESIGN_EDGES), code_envelope(code))
    unexpected = by_type(report, "unexpected_component")
    assert [f.source_component for f in unexpected] == ["legacy"]
    f = unexpected[0]
    assert f.severity == "medium"  # có phụ thuộc vào component thiết kế
    assert any("legacy/export.py" in e.file_path for e in f.evidence)
    assert "code:legacy" in report.drift.changed_relations["added_components"]


def test_4b_phantom_package_is_reported(agent):
    """Import tới package nội bộ không tồn tại (kiểu lỗi cấy trong fault_src của SCA)."""
    code = dict(CONSISTENT_CODE)
    code["models.order"] = ["dataclasses", "src.infrastructure.database"]
    report = agent.check(design_graph(DESIGN_NODES, DESIGN_EDGES), code_envelope(code))
    f = next(x for x in by_type(report, "unexpected_component") if x.source_component == "infrastructure")
    assert f.severity == "high" and "KHÔNG tồn tại" in f.description
    assert f.line_number == 2


# ---------------------------------------------------------------- Test 5
def test_5_architecture_drift(agent):
    drifted = {
        "api.routes": ["storage.db", "models.order"],        # bỏ qua services
        "services.orders": ["models.order", "api.routes"],    # ngược chiều api
        "storage.db": ["models.order", "services.orders"],    # ngược chiều services
        "models.order": ["storage.db"],                        # ngược chiều storage
    }
    baseline = agent.check(design_graph(DESIGN_NODES, DESIGN_EDGES), code_envelope(CONSISTENT_CODE))
    report = agent.check(design_graph(DESIGN_NODES, DESIGN_EDGES), code_envelope(drifted),
                         rules(default_policy="design_only"))
    d = report.drift
    assert d.drift_score > baseline.drift.drift_score
    assert d.drift_score >= 0.4 and d.drift_level in ("high", "severe")
    assert d.divergences >= 4 and d.absences >= 1
    assert "api -> storage" in d.changed_relations["added_dependencies"]
    assert "api -> services" in d.changed_relations["removed_dependencies"]
    types = {f.violation_type for f in report.findings}
    assert {"reverse_dependency", "unexpected_dependency", "missing_dependency", "cyclic_dependency"} <= types
    assert d.violation_index > 0
    # công thức: similarity = (|N∩| + |E∩|) / (|N∪| + |E∪|)
    n_inter, n_union = d.matched_components, d.design_components + d.actual_components - d.matched_components
    e_union = d.design_dependencies + d.actual_dependencies - d.convergences
    assert d.structural_similarity == pytest.approx((n_inter + d.convergences) / (n_union + e_union), abs=1e-3)


# ---------------------------------------------------------------- Test 6
def test_6_uncertain_mapping_is_not_concluded(agent):
    """Tên chỉ gần giống, không có bằng chứng cấu trúc -> không được khẳng định."""
    design = design_graph([("payment", "Payment Processor"), ("catalog", "Catalog")], [])
    code = code_envelope({"paymnt_proc.gateway": ["requests"], "catalog.items": []})
    report = agent.check(design, code)
    m = mapping(report, "payment")
    assert m.status == "uncertain"
    assert m.code_packages == ["paymnt_proc"]
    assert m.confidence < report.strategy.mapping_accept_threshold
    assert m.structure_score is None and m.method.endswith("_only")
    f = by_type(report, "uncertain_mapping")
    assert len(f) == 1 and f[0].status == "needs_review"
    # không đồng thời kết luận "thiếu component" cho thứ đang được đề xuất ánh xạ
    assert not [x for x in by_type(report, "missing_component") if x.source_component == "payment"]
    assert mapping(report, "catalog").status == "confirmed"


def test_6b_uncertain_mapping_lowers_violation_confidence(agent):
    """Vi phạm dính tới ánh xạ chưa chắc chắn phải ở trạng thái cần xác nhận."""
    design = design_graph([("web", "Web Frontend"), ("billing", "Billing Engine")], [])
    code = code_envelope({"frontend.views": ["bill_eng.core"], "bill_eng.core": []})
    r = rules({"rule_id": "R1", "kind": "forbid", "source": "web", "target": "billing"})
    report = agent.check(design, code, r)
    assert mapping(report, "billing").status == "uncertain"
    f = by_type(report, "forbidden_dependency")
    assert len(f) == 1
    assert f[0].status == "needs_review"
    assert f[0].confidence_breakdown.mapping < 0.75


def test_6c_structure_resolves_renamed_component(agent):
    """Tên khác hẳn nhưng cấu trúc khớp -> tối đa 'uncertain' (structure_only), không tự khẳng định."""
    design = design_graph([("api", "API"), ("engine", "Recommendation Engine"), ("models", "Models")],
                          [("api", "engine"), ("engine", "models")])
    code = code_envelope({"api.views": ["zeta.core"], "zeta.core": ["models.user"], "models.user": []})
    report = agent.check(design, code)
    m = mapping(report, "engine")
    assert m.code_packages == ["zeta"]
    assert m.status == "uncertain" and m.method == "structure_only"
    assert m.structure_score == 1.0


def test_single_file_components_in_one_root_package(agent):
    """Hồi quy (phát hiện khi chạy trên code thật của Coordinator): component là FILE ĐƠN trong một package gốc
    (coordinator_agent/schema.py...) phải được ánh xạ; module chưa ánh xạ được báo đúng tên; __init__ gốc bị bỏ qua."""
    design = design_graph([("orchestrator", "Coordinator Agent"), ("delta", "Delta Tracking"), ("schema", "Schema"),
                           ("config", "Config")],
                          [("orchestrator", "delta"), ("orchestrator", "config"), ("delta", "schema")])
    code = code_envelope({
        "coord.agent": ["coord.delta.analyzer", "coord.config", "coord.store"],
        "coord.delta.analyzer": ["coord.schema"],
        "coord.schema": ["pydantic"],
        "coord.config": [],
        "coord.store": ["sqlite3"],
    })
    report = agent.check(design, code)
    got = {m.design_id: m.code_packages for m in report.mappings}
    assert got == {"orchestrator": ["coord.agent"], "delta": ["coord.delta"], "schema": ["coord.schema"],
                   "config": ["coord.config"]}
    assert not by_type(report, "missing_component") and not by_type(report, "reverse_dependency")
    junk = [f.source_component for f in by_type(report, "unexpected_component")]
    assert junk == ["coord.store"]  # không phải "coord", không báo __init__.py gốc


def test_missing_component_cannot_steal_file_of_other_component(agent):
    """Hồi quy: component thiếu không được 'giành' file con trong package của component khác chỉ nhờ cấu trúc."""
    design = design_graph([("svc", "Services"), ("repo", "Repositories"), ("notify", "Notifications")],
                          [("svc", "repo"), ("svc", "notify")])
    code = code_envelope({"services.loan": ["repositories.book_repo"], "repositories.book_repo": []})
    report = agent.check(design, code)
    assert mapping(report, "notify").status == "unmapped"
    assert mapping(report, "repo").code_packages == ["repositories"]


def test_parent_package_wins_over_same_named_child_file(agent):
    design = design_graph([("dependency_injection", "Dependency Injection"), ("api", "API")],
                          [("api", "dependency_injection")])
    code = code_envelope({"api.views": ["di.dependency_injection", "di.unit_of_work"],
                          "di.dependency_injection": [], "di.unit_of_work": []})
    report = agent.check(design, code)
    assert mapping(report, "dependency_injection").code_packages == ["di"]
    assert not by_type(report, "unexpected_component")
