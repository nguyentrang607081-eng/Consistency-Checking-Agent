"""Kiểm tra tích hợp THẬT với code của các agent khác trong nhóm.

Script chạy CCA trên dữ liệu demo rồi đưa kết quả vào:
  1. Coordinator Agent      (repo tranganhne/coordinator-agent)       -> CoordinatorAgent.run(...)
  2. Adaptive Recommendation (repo huainguyn/Adaptive-Recommendation-Agent) -> RecommendationRequest + workflow
và in ra kết quả. Coordinator cũng sinh downstream payload cho CCA -> chạy lại CCA với chỉ thị đó
(đóng vòng điều phối Coordinator -> CCA).

Cách dùng (clone các repo của nhóm cạnh repo này):
    python scripts/integration_check.py --coordinator ../coordinator-agent \
        --recommendation ../Adaptive-Recommendation-Agent
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from consistency_agent.agent import ConsistencyCheckingAgent  # noqa: E402
from consistency_agent.integration.coordinator import directive_from_coordinator, to_coordinator_input  # noqa: E402
from consistency_agent.integration.recommendation import to_recommendation_request  # noqa: E402


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def run_cca(kb: str, scan_id: str, code_file: str, strategy: dict | None = None):
    ex = ROOT / "examples" / "py_clean_arch"
    return ConsistencyCheckingAgent(knowledge=kb).check(
        load(ex / "design_architecture.json"), load(ex / code_file), load(ex / "rules.json"), strategy,
        {"project_id": "py-clean-arch", "scan_id": scan_id})


def check_coordinator(repo: Path) -> None:
    print(f"\n### Coordinator Agent: {repo}")
    sys.path.insert(0, str(repo))
    from coordinator_agent.agent import CoordinatorAgent  # type: ignore

    with tempfile.TemporaryDirectory() as tmp:
        cca_kb, coord_kb = f"{tmp}/cca.sqlite", f"{tmp}/coord.sqlite"
        coord = CoordinatorAgent(kb_path=coord_kb)
        r1 = run_cca(cca_kb, "scan-1", "code_clean.json")
        res1 = asyncio.run(coord.run(to_coordinator_input(r1)))
        p1 = res1.reporting_payload
        print(f"scan-1: CCA gửi {len([f for f in r1.findings if f.violation_type != 'uncertain_mapping'])} finding"
              f" -> Coordinator: delta={p1.delta_summary.model_dump()} kb_persisted={p1.kb_persisted} "
              f"warnings={len(p1.warnings)} errors={p1.errors}")

        r2 = run_cca(cca_kb, "scan-2", "code_faulty.json")
        res2 = asyncio.run(coord.run(to_coordinator_input(r2, baseline_scan_id="scan-1")))
        p2 = res2.reporting_payload
        print(f"scan-2: Coordinator delta={p2.delta_summary.model_dump()} drift_score={p2.drift_score} "
              f"kb_persisted={p2.kb_persisted} conflicts={len(p2.conflicts)}")
        for f in p2.findings[:6]:
            print(f"   {f.delta_status:10} {f.type:20} {f.severity:8} {f.location.source_component} -> "
                  f"{f.location.target_component} conf={f.confidence}")
        print(f"   strategy_proposal: {p2.strategy_proposal.model_dump()}")

        down = res2.downstream_payloads.get("consistency_checking")
        print(f"Coordinator -> CCA downstream payload: {down}")
        directive = directive_from_coordinator(down, p2.strategy_proposal.notes)
        r3 = run_cca(cca_kb, "scan-3", "code_faulty.json", directive.model_dump(exclude_none=True) if directive else None)
        print(f"scan-3 (theo chỉ thị Coordinator): scope={r3.strategy.scope} threshold={r3.strategy.confidence_threshold} "
              f"llm_review={directive.llm_review if directive else None} source={r3.strategy.source}")
        for reason in r3.strategy.reasons:
            print(f"   - {reason}")
        print(f"   4 kịch bản: {r3.scenario_report.verdict} / {r3.scenario_report.primary_scenario} {r3.scenario_report.counts}")
        assert p1.errors == [] and p2.errors == [], "Coordinator báo lỗi"
        assert not any("sai schema" in w for w in p1.warnings + p2.warnings), "Coordinator từ chối finding"
    sys.path.remove(str(repo))
    print("=> Coordinator nhận đủ finding, không có finding nào bị loại vì sai schema.")


def check_recommendation(repo: Path) -> None:
    print(f"\n### Adaptive Recommendation Agent: {repo}")
    sys.path.insert(0, str(repo))
    for mod in ("schema", "agent", "recommender"):
        sys.modules.pop(mod, None)
    schema = importlib.import_module("schema")
    agent_mod = importlib.import_module("agent")
    r = run_cca(":memory:", "scan-r", "code_faulty.json")
    req = to_recommendation_request(r)
    schema.RecommendationRequest.model_validate(req)  # extra="forbid": sai 1 trường là lỗi ngay
    print(f"RecommendationRequest hợp lệ: {len(req['issues'])} issue")
    runner = getattr(agent_mod, "run_agent", None) or getattr(agent_mod, "run_recommendation", None)
    if runner:
        out = runner(req)
        out = out if isinstance(out, dict) else out.model_dump()
        print(f"Recommendation status={out.get('status')} recs={len(out.get('recommendations', []))} "
              f"next={out.get('next_action_suggestion', {}).get('action')}")
    else:
        from recommender import build_recommendation_response  # type: ignore
        resp = build_recommendation_response(schema.RecommendationRequest.model_validate(req))
        print(f"Recommendation status={resp.status} recs={len(resp.recommendations)} "
              f"next={resp.next_action_suggestion.action}")
    sys.path.remove(str(repo))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--coordinator", type=Path)
    ap.add_argument("--recommendation", type=Path)
    a = ap.parse_args()
    if not a.coordinator and not a.recommendation:
        ap.error("Cần ít nhất --coordinator hoặc --recommendation")
    if a.coordinator:
        check_coordinator(a.coordinator.resolve())
    if a.recommendation:
        check_recommendation(a.recommendation.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
