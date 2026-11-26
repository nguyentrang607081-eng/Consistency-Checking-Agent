"""Node dùng trong LangGraph Orchestration cấp hệ thống (5 agent).

Hợp đồng state (các khóa đề xuất cho workflow chung của nhóm):

  Đọc:
    architecture_graph : dict  - đầu ra Architecture Extraction Agent (ArchitectureGraph)
    code_envelope      : dict  - đầu ra Source Code Analysis Agent (SourceCodeEnvelope)
    rules              : dict? - RuleSet (tùy chọn)
    cca_strategy       : dict? - chỉ thị từ Coordinator, vd downstream_payloads["consistency_checking"]
    context            : dict? - {"project_id", "scan_id", "commit_hash"}
  Ghi:
    consistency_report : dict  - ConsistencyReport (JSON) -> Reporting Agent
    coordinator_input  : dict  - CoordinatorInput (JSON) -> Coordinator Agent

Ví dụ ghép vào StateGraph của nhóm:

    from consistency_agent.integration.langgraph_node import make_node
    graph.add_node("consistency_checking", make_node(kb_path="data/cca_knowledge.sqlite"))
    graph.add_edge("code_analysis", "consistency_checking")
    graph.add_edge("consistency_checking", "coordinator")
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from ..agent import ConsistencyCheckingAgent
from ..config import AgentConfig
from .coordinator import to_coordinator_input


def make_node(kb_path: Optional[str] = None, config: Optional[AgentConfig] = None
              ) -> Callable[[dict[str, Any]], dict[str, Any]]:
    agent = ConsistencyCheckingAgent(config=config, knowledge=kb_path)

    def consistency_checking_node(state: dict[str, Any]) -> dict[str, Any]:
        report = agent.check(state.get("architecture_graph"), state.get("code_envelope"), state.get("rules"),
                             state.get("cca_strategy"), state.get("context"))
        return {"consistency_report": report.model_dump(mode="json"),
                "coordinator_input": to_coordinator_input(report)}

    return consistency_checking_node
