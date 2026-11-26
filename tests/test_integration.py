"""Kiểm thử tích hợp: hợp đồng JSON với Coordinator / Recommendation, dữ liệu demo, CLI, node LangGraph."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from consistency_agent.__main__ import main as cli_main
from consistency_agent.agent import ConsistencyCheckingAgent
from consistency_agent.integration.coordinator import directive_from_coordinator, to_coordinator_input
from consistency_agent.integration.langgraph_node import make_node
from consistency_agent.integration.recommendation import to_recommendation_request
from consistency_agent.schemas.outputs import ConsistencyReport
from tests.helpers import by_type, code_envelope, design_graph, rules

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples"

# Hợp đồng của coordinator_agent/schema.py (repo tranganhne/coordinator-agent)
COORD_TYPES = {"forbidden_dependency", "missing_dependency", "divergence", "absence"}
COORD_SOURCES = {"rule_engine", "graph_matching", "similarity"}
COORD_FINDING_KEYS = {"id", "rule_id", "type", "severity", "source_component", "target_component", "file",
                      "line", "evidence", "confidence", "source"}


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def faulty_report() -> ConsistencyReport:
    ex = EX / "py_clean_arch"
    return ConsistencyCheckingAgent(knowledge=False).check(
        load(ex / "design_architecture.json"), load(ex / "code_faulty.json"), load(ex / "rules.json"))


def test_demo_py_clean_arch_detects_all_injected_faults(faulty_report):
    """fault_src của SCA cấy 3 lỗi: OP1 & OP2 (controllers -> src.infrastructure), OP3 (models -> src.domain)."""
    r = faulty_report
    phantom = {f.source_component: f for f in by_type(r, "unexpected_component")}
    assert {"infrastructure", "domain"} <= set(phantom)
    files = {e.file_path for f in phantom.values() for e in f.evidence}
    assert any(p.endswith("controllers/rest/extension.py") for p in files)          # OP1
    assert any(p.endswith("controllers/graphql/pokemon/mapper.py") for p in files)  # OP2
    assert any(p.endswith("models/pokemon.py") for p in files)                      # OP3
    for m in r.mappings:
        if m.design_type not in ("actor", "database"):
            assert m.status == "confirmed", m


def test_demo_py_clean_arch_clean_version_is_mostly_consistent():
    ex = EX / "py_clean_arch"
    r = ConsistencyCheckingAgent(knowledge=False).check(
        load(ex / "design_architecture.json"), load(ex / "code_clean.json"), load(ex / "rules.json"))
    assert not by_type(r, "unexpected_component")
    assert r.drift.drift_level in ("none", "low")
    # Phát hiện thật trong repo gốc: usecases import di.unit_of_work (layer composition)
    assert [(f.source_component, f.target_component) for f in by_type(r, "layer_violation")] == \
        [("usecases", "dependency_injection")]


def test_demo_ecommerce_from_aea_sample():
    ex = EX / "ecommerce"
    r = ConsistencyCheckingAgent(knowledge=False).check(
        load(ex / "design_architecture.json"), load(ex / "code_envelope.json"), load(ex / "rules.json"))
    assert {m.design_id: m.code_packages for m in r.mappings if m.code_packages} == {
        "web_app": ["webapp"], "api_gateway": ["gateway"], "order_service": ["orders"],
        "notification_service": ["notify"]}
    assert [(f.source_component, f.target_component) for f in by_type(r, "forbidden_dependency")] == \
        [("web_app", "order_service")]
    assert by_type(r, "reverse_dependency")[0].source_component == "notification_service"
    assert by_type(r, "unexpected_component")[0].source_component == "analytics"


def test_coordinator_contract(faulty_report):
    payload = to_coordinator_input(faulty_report, baseline_scan_id="s0")
    assert set(payload) == {"context", "consistency_result"}
    ctx = payload["context"]
    assert ctx["scan_scope"] in ("full", "incremental") and ctx["baseline_scan_id"] == "s0"
    findings = payload["consistency_result"]["findings"]
    assert findings
    for f in findings:
        assert set(f) == COORD_FINDING_KEYS
        assert f["type"] in COORD_TYPES and f["source"] in COORD_SOURCES
        assert 0.0 <= f["confidence"] <= 1.0
        assert f["severity"] in ("low", "medium", "high", "critical")
        assert isinstance(f["line"], int) and isinstance(f["file"], str)
    assert payload["consistency_result"]["drift_score"] == faulty_report.drift.drift_score
    json.dumps(payload)  # serialize được


def test_coordinator_directive_roundtrip():
    # đúng định dạng build_for("consistency_checking", strategy) của Coordinator
    d = directive_from_coordinator({"confidence_threshold": 0.8, "target_modules": ["controllers"]})
    assert d.confidence_threshold == 0.8 and d.target_modules == ["controllers"]
    assert directive_from_coordinator(None) is None


def test_recommendation_contract(faulty_report):
    req = to_recommendation_request(faulty_report)
    assert {"schema_version", "project_id", "run_id", "confidence_threshold", "architecture_rules", "issues"} <= set(req)
    for issue in req["issues"]:
        assert issue["status"] in ("COMPLIANT", "VIOLATION", "REVIEW")
        assert issue["rule_status"] in ("ALLOWED", "FORBIDDEN", "NOT_SPECIFIED")
        assert issue["severity"] in ("CRITICAL", "HIGH", "MEDIUM", "LOW")
        for ev in issue["evidence"]:
            assert ev["line_start"] is None or ev["line_start"] >= 1


def test_report_json_roundtrip(faulty_report):
    text = faulty_report.model_dump_json()
    again = ConsistencyReport.model_validate_json(text)
    assert again.findings == faulty_report.findings
    assert again.visualization.nodes and again.visualization.edges
    for f in faulty_report.findings:
        assert f.finding_id.startswith("CCA-") and len(f.fingerprint) == 16
        b = f.confidence_breakdown
        assert f.confidence_score == pytest.approx(b.mapping * b.rule_clarity * b.evidence * b.data_completeness,
                                                   abs=1e-3)


def test_langgraph_node_and_async_api():
    agent_state = {"architecture_graph": design_graph([("a", "A"), ("b", "B")], [("a", "b")]),
                   "code_envelope": code_envelope({"a.x": ["b.y"], "b.y": []}),
                   "context": {"project_id": "p", "scan_id": "s"}}
    node = make_node(kb_path=":memory:")
    out = node(agent_state)
    assert out["consistency_report"]["status"] == "SUCCESS"
    assert out["coordinator_input"]["context"]["scan_id"] == "s"
    res = asyncio.run(ConsistencyCheckingAgent(knowledge=False).run(agent_state))
    assert res["drift"]["drift_score"] == 0.0


def test_invalid_input_returns_failed_report():
    r = ConsistencyCheckingAgent(knowledge=False).check({"nodes": [{"name": "no id"}]}, {"payload": {}})
    assert r.status == "FAILED" and r.errors


def test_cli_check_and_feedback(tmp_path):
    ex = EX / "ecommerce"
    out, coord, kb = tmp_path / "r.json", tmp_path / "c.json", tmp_path / "kb.sqlite"
    code = cli_main(["check", "--design", str(ex / "design_architecture.json"), "--code",
                     str(ex / "code_envelope.json"), "--rules", str(ex / "rules.json"), "--kb", str(kb),
                     "-o", str(out), "--coordinator-output", str(coord), "-q"])
    assert code == 0 and out.exists() and coord.exists()
    report = load(out)
    fp = next(f["fingerprint"] for f in report["findings"] if f["violation_type"] == "forbidden_dependency")
    assert cli_main(["feedback", "accept", "--project", report["project_id"], "--fingerprint", fp,
                     "--kb", str(kb)]) == 0
    cli_main(["check", "--design", str(ex / "design_architecture.json"), "--code", str(ex / "code_envelope.json"),
              "--rules", str(ex / "rules.json"), "--kb", str(kb), "--scan-id", "again", "-o", str(out), "-q"])
    again = load(out)
    assert next(f for f in again["findings"] if f["fingerprint"] == fp)["status"] == "accepted"


def test_cli_extract_code_fallback(tmp_path):
    out = tmp_path / "env.json"
    assert cli_main(["extract-code", str(EX / "ecommerce" / "src"), "-o", str(out)]) == 0
    env = load(out)
    assert env["status"] == "SUCCESS" and len(env["payload"]["dependencies"]) == 9


def test_rules_unknown_field_is_rejected():
    r = ConsistencyCheckingAgent(knowledge=False).check(design_graph(["a"], []), code_envelope({"a.x": []}),
                                                        rules(unknown_field=1))
    assert r.status == "FAILED"
