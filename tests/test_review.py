"""Kiểm thử 4 kịch bản + RAG + few-shot + LLM.

Đường LLM được kiểm thử với một SERVER GIẢ LẬP chuẩn OpenAI /chat/completions chạy cục bộ (không gọi
API thật, không cần key). Như vậy kiểm tra được toàn bộ: dựng prompt, gửi HTTP, parse JSON, cache,
ngân sách, fallback khi lỗi, policy resolve.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from consistency_agent.adaptive.knowledge import KnowledgeStore
from consistency_agent.agent import ConsistencyCheckingAgent
from consistency_agent.config import AgentConfig
from consistency_agent.integration.coordinator import directive_from_coordinator
from consistency_agent.llm.providers import OpenAICompatLLM, create_reviewer, parse_review
from consistency_agent.rag.fewshot import CURATED, FewShotSelector
from consistency_agent.rag.retriever import BM25Index, load_corpus

ROOT = Path(__file__).resolve().parent.parent
SC = ROOT / "examples" / "scenarios"


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


DESIGN, RULES = load(SC / "design_architecture.json"), load(SC / "rules.json")


def code(name: str) -> dict:
    return load(SC / name / "code_envelope.json")


# ------------------------------------------------------------------ mock LLM server
class MockLLM:
    def __init__(self, mode: str = "confirm") -> None:
        self.mode = mode
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        handler = self._handler()
        self.server = HTTPServer(("127.0.0.1", 0), handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def _handler(self):
        mock = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # noqa: D401 - im lặng
                pass

            def do_POST(self):  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append(body)
                mock.headers.append(dict(self.headers))
                last = body["messages"][-1]["content"]
                if "FINDING" not in last:
                    content = "Tóm tắt từ LLM giả lập: kịch bản chính là code sai so với thiết kế."
                elif mock.mode == "garbage":
                    content = "xin lỗi, tôi không chắc"
                else:
                    finding = json.loads(last.split("FINDING:\n", 1)[1])
                    scen = last.split("Kịch bản do quy tắc tất định gán: ", 1)[1].split("\n")[0]
                    content = "Kết quả:\n" + json.dumps({
                        "scenario": scen, "verdict": "confirm", "confidence": 0.9,
                        "rationale": f"LLM giả lập xác nhận {finding['violation_type']}.",
                        "suggested_fix": "Sửa theo tài liệu.",
                        "cited_docs": ["scenarios#kich-ban-2-code-sai-so-voi-thiet-ke", "doc-bịa-không-tồn-tại"]})
                out = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)
        return H

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def mock_llm():
    m = MockLLM()
    yield m
    m.close()


def llm_agent(mock: MockLLM, kb=False, **cfg) -> ConsistencyCheckingAgent:
    reviewer = OpenAICompatLLM(mock.url, "mock-model", api_key="test-key", name="openai_compat")
    return ConsistencyCheckingAgent(AgentConfig(**cfg), knowledge=kb, reviewer=reviewer)


# ------------------------------------------------------------------ 4 kịch bản
@pytest.mark.parametrize("name", ["1_match", "2_wrong", "3_missing", "4_junk"])
def test_four_scenarios_dataset(name):
    expected = load(SC / "expected.json")[name]
    r = ConsistencyCheckingAgent(knowledge=False).check(DESIGN, code(name), RULES)
    sr = r.scenario_report
    assert (sr.verdict, sr.primary_scenario) == (expected["verdict"], expected["primary_scenario"])
    for f in r.findings:
        assert f.scenario is not None
    if name == "1_match":
        assert r.findings == [] and sr.match_ratio == 1.0 and r.drift.drift_score == 0.0
    if name == "2_wrong":
        assert {(i.source, i.target) for i in sr.wrong} >= {("api", "repositories"), ("models", "services")}
        assert not sr.missing and not sr.junk
    if name == "3_missing":
        assert {i.source for i in sr.missing} == {"notifications", "services"}
        assert not sr.wrong and not sr.junk
    if name == "4_junk":
        details = {i.source: i.detail for i in sr.junk if i.kind == "component"}
        assert details == {"cache": "phantom", "legacy": "orphan", "utils": "undocumented"}
        assert not sr.wrong and not sr.missing


def test_scenario_summary_is_grounded():
    r = ConsistencyCheckingAgent(knowledge=False).check(DESIGN, code("2_wrong"), RULES)
    assert "code sai so với thiết kế" in r.scenario_report.summary
    assert "api -> repositories" in r.scenario_report.summary
    assert r.scenario_report.summary_by == "template"


# ------------------------------------------------------------------ RAG + few-shot (offline)
def test_rag_retrieves_relevant_knowledge():
    idx = BM25Index(load_corpus())
    top = idx.search("missing_dependency sync_call gRPC giữa hai service", k=1)[0][0]
    assert top.doc_id.startswith("static_analysis_limits#")
    top = idx.search("unexpected_component orphan code chết", k=1)[0][0]
    assert top.doc_id.startswith("scenarios#kich-ban-4")


def test_fewshot_selects_same_type_examples():
    sel = FewShotSelector(CURATED)
    picked = sel.select({"violation_type": "unexpected_component", "scenario_detail": "phantom",
                         "source": "infra", "description": "import tới package không tồn tại"}, k=3)
    assert picked[0][0].example_id == "fs-junk-2"
    assert all(e.input["violation_type"] == "unexpected_component" for e, _ in picked)


def test_local_review_is_offline_and_labelled_not_llm():
    r = ConsistencyCheckingAgent(knowledge=False).check(DESIGN, code("4_junk"), RULES)
    assert r.review.mode == "local" and r.review.llm_calls == 0 and r.performance.llm_calls == 0
    for f in r.findings:
        lr = f.llm_review
        assert lr is not None and lr.is_llm is False and lr.provider == "local-fewshot"
        assert lr.few_shot_ids and lr.retrieved_docs
    assert r.review.disagreements == 0


def test_feedback_becomes_case_memory_and_few_shot():
    kb = KnowledgeStore(":memory:")
    agent = ConsistencyCheckingAgent(knowledge=kb)
    r1 = agent.check(DESIGN, code("4_junk"), RULES, context={"project_id": "lib", "scan_id": "s1"})
    legacy = next(f for f in r1.findings if f.source_component == "legacy" and f.violation_type == "unexpected_component")
    agent.mark_false_positive("lib", legacy.fingerprint, "legacy là công cụ xuất báo cáo chạy tay")
    r2 = agent.check(DESIGN, code("4_junk"), RULES, context={"project_id": "lib", "scan_id": "s2"})
    assert r2.review.rag_documents == r1.review.rag_documents + 1     # thêm 1 case:<fingerprint>
    assert r2.review.few_shot_pool == r1.review.few_shot_pool + 1     # thêm 1 ví dụ fb-...
    dep = next(f for f in r2.findings if f.source_component == "legacy" and f.target_component == "models")
    assert any(i.startswith("fb-") for i in dep.llm_review.few_shot_ids)


def test_disable_review():
    r = ConsistencyCheckingAgent(knowledge=False, reviewer=False).check(DESIGN, code("2_wrong"), RULES)
    assert r.review.mode == "off" and all(f.llm_review is None for f in r.findings)
    assert r.scenario_report.primary_scenario == "wrong"


# ------------------------------------------------------------------ LLM (mock server)
def test_llm_review_prompt_contains_fewshot_and_rag(mock_llm):
    r = llm_agent(mock_llm).check(DESIGN, code("2_wrong"), RULES)
    assert r.review.mode == "llm" and r.review.reviewed == 3
    assert r.review.llm_calls == 4  # 3 finding + 1 tóm tắt
    assert r.performance.llm_calls == 4
    req = mock_llm.requests[0]
    roles = [m["role"] for m in req["messages"]]
    assert roles[0] == "system" and roles.count("assistant") == 3      # 3 ví dụ few-shot dạng hội thoại
    assert "TÀI LIỆU THAM KHẢO" in req["messages"][-1]["content"]      # ngữ cảnh RAG
    assert req["temperature"] == 0 and req["model"] == "mock-model"
    assert mock_llm.headers[0]["Authorization"] == "Bearer test-key"
    f = r.findings[0]
    assert f.llm_review.is_llm and f.llm_review.verdict == "confirm"
    assert "doc-bịa-không-tồn-tại" not in f.llm_review.cited_docs     # id tài liệu bịa bị loại
    assert r.scenario_report.summary_by == "mock-model"


def test_llm_cache_avoids_repeat_calls(mock_llm):
    kb = KnowledgeStore(":memory:")
    agent = llm_agent(mock_llm, kb=kb)
    agent.check(DESIGN, code("2_wrong"), RULES, context={"project_id": "lib", "scan_id": "s1"})
    calls = len(mock_llm.requests)
    r2 = agent.check(DESIGN, code("2_wrong"), RULES, context={"project_id": "lib", "scan_id": "s2"})
    assert r2.review.cache_hits == 3
    assert len(mock_llm.requests) == calls + 1  # chỉ gọi lại phần tóm tắt
    assert all(f.llm_review.cached for f in r2.findings)


def test_llm_budget_limits_calls(mock_llm):
    r = llm_agent(mock_llm, llm_max_calls=1).check(DESIGN, code("4_junk"), RULES)
    assert r.review.llm_calls == 1
    providers = [f.llm_review.provider for f in r.findings]
    assert providers.count("openai_compat") == 1 and providers.count("local-fewshot") == len(providers) - 1


def test_llm_garbage_falls_back_to_local(mock_llm):
    mock_llm.mode = "garbage"
    r = llm_agent(mock_llm).check(DESIGN, code("3_missing"), RULES)
    assert r.review.fallbacks >= 2 and r.review.errors
    assert all(f.llm_review.provider == "local-fewshot" for f in r.findings)


def test_resolve_policy_only_upgrades_with_real_llm(mock_llm):
    r = llm_agent(mock_llm, llm_policy="resolve").check(DESIGN, code("3_missing"), RULES)
    assert r.review.status_changes == 2
    assert all(f.status == "confirmed" and f.llm_review.status_changed for f in r.findings)
    local = ConsistencyCheckingAgent(AgentConfig(llm_policy="resolve"), knowledge=False).check(
        DESIGN, code("3_missing"), RULES)
    assert local.review.status_changes == 0  # bộ cục bộ không được đổi trạng thái


def test_coordinator_llm_hint_doubles_budget(mock_llm):
    directive = directive_from_coordinator({"confidence_threshold": 0.7, "target_modules": []},
                                           notes=["60% finding dưới ngưỡng tin cậy 0.7; nên bật kiểm tra lại bằng LLM ở lần sau."])
    assert directive.llm_review is True
    r = llm_agent(mock_llm, llm_max_calls=5).check(DESIGN, code("2_wrong"), RULES, strategy=directive.model_dump())
    assert r.strategy.llm_budget == 10 and any(x.startswith("P10") for x in r.strategy.reasons)


def test_missing_api_key_falls_back_with_warning(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("CCA_LLM_API_KEY", raising=False)
    with pytest.raises(ValueError):
        create_reviewer("groq")
    r = ConsistencyCheckingAgent(knowledge=False, reviewer="groq").check(DESIGN, code("2_wrong"), RULES)
    assert r.review.mode == "local" and any("GROQ_API_KEY" in w for w in r.warnings)


def test_parse_review_validates_output():
    ok = parse_review('```json\n{"scenario":"junk","verdict":"confirm","confidence":1.7,"cited_docs":["a","x"]}\n```', {"a"})
    assert ok["confidence"] == 1.0 and ok["cited_docs"] == ["a"]
    with pytest.raises(ValueError):
        parse_review('{"scenario":"bad","verdict":"confirm"}', set())


def test_report_is_deterministic_across_hash_seeds():
    """Kết quả (kể cả fingerprint) không được phụ thuộc thứ tự set/dict giữa các lần chạy Python."""
    import os
    import subprocess
    import sys
    script = (
        "import json,sys\n"
        "from consistency_agent.agent import ConsistencyCheckingAgent\n"
        "L=lambda p: json.load(open(p, encoding='utf-8'))\n"
        "out=[]\n"
        "for n in ['2_wrong','3_missing','4_junk']:\n"
        "    r=ConsistencyCheckingAgent(knowledge=False).check(L('examples/scenarios/design_architecture.json'),"
        "L(f'examples/scenarios/{n}/code_envelope.json'),L('examples/scenarios/rules.json'))\n"
        "    d=r.model_dump(mode='json', exclude={'generated_at','performance','adaptation_trace'})\n"
        "    out.append(d)\n"
        "print(json.dumps(out, sort_keys=True))\n")
    results = set()
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        res = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env, capture_output=True, text=True,
                             check=True)
        results.add(res.stdout)
    assert len(results) == 1


def test_unreachable_llm_trips_circuit_breaker():
    """LLM không kết nối được -> chỉ thử 1 lần rồi dùng bộ cục bộ cho phần còn lại (không treo)."""
    import socket
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # cổng đóng -> connection refused
    reviewer = OpenAICompatLLM(f"http://127.0.0.1:{port}/v1", "x", timeout=2)
    r = ConsistencyCheckingAgent(knowledge=False, reviewer=reviewer).check(DESIGN, code("4_junk"), RULES)
    assert r.review.llm_calls == 1 and r.review.fallbacks == 1
    assert r.review.local_fallback_reviews == len(r.findings)
    assert all(f.llm_review.provider == "local-fewshot" for f in r.findings)


def test_empty_inputs_are_inconclusive_not_match():
    r = ConsistencyCheckingAgent(knowledge=False).check({"nodes": [], "edges": []}, {"payload": {"dependencies": []}})
    assert r.scenario_report.verdict == "inconclusive"
    assert any("không có component" in w for w in r.warnings)
