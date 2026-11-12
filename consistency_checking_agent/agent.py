"""LangGraph orchestration for the consistency checking pipeline."""
import asyncio
from typing import TypedDict, Any
from langgraph.graph import StateGraph, END
from .schema import ConsistencyInput, ConsistencyReport
from .rule_loader import load_rules
from .element_mapper import map_code_to_architecture, lift_edges
from .checkers.layer_checker import check_layers
from .checkers.module_checker import check_cross_module
from .checkers.cycle_checker import check_cycles
from .checkers.drift_checker import check_drift
from .aggregator import aggregate

class AgentState(TypedDict, total=False):
    input_payload: dict[str, Any]
    parsed: ConsistencyInput
    rules: Any
    mapping: list
    lifted_edges: list
    raw_violations: list
    drift_candidates: list
    warnings: list[str]
    errors: list[str]
    final_report: ConsistencyReport

def _node_load_and_map(state: AgentState):
    payload = ConsistencyInput.model_validate(state["input_payload"])
    rules = load_rules("config/rules.yaml", payload.rules)
    mapping = map_code_to_architecture(payload.code_graph, payload.architecture_graph,
                                       float(__import__("os").getenv("MAPPING_MIN_SCORE", "0.85")))
    lifted = lift_edges(payload.code_graph, mapping)
    warnings = list(payload.architecture_graph.warnings)
    if len(lifted) < len(payload.code_graph.edges):
        warnings.append(f"{len(payload.code_graph.edges)-len(lifted)} code edges were excluded because one or both endpoints were unmapped.")
    return {"parsed": payload, "rules": rules, "mapping": mapping, "lifted_edges": lifted,
            "warnings": warnings, "errors": []}

async def _node_run_checkers(state: AgentState):
    p, rules, edges = state["parsed"], state["rules"], state["lifted_edges"]
    arch = p.architecture_graph
    jobs = [
        asyncio.to_thread(check_layers, edges, arch, rules),
        asyncio.to_thread(check_cross_module, edges, rules),
        asyncio.to_thread(check_cycles, edges),
        asyncio.to_thread(check_drift, edges, arch, rules, state["mapping"]),
    ]
    results = await asyncio.gather(*jobs, return_exceptions=True)
    violations, candidates, warnings = [], [], list(state.get("warnings", []))
    for index, result in enumerate(results):
        if isinstance(result, Exception):
            warnings.append(f"Checker {index} failed: {type(result).__name__}: {result}")
            continue
        if index == 3:
            drift_violations, drift_candidates = result
            violations.extend(drift_violations)
            candidates.extend(drift_candidates)
        else:
            violations.extend(result)
    decisions = {(d.source_id, d.target_id): d.decision for d in p.human_decisions}
    remaining = []
    for candidate in candidates:
        decision = decisions.get((candidate["source_id"], candidate.get("target_id")))
        if decision == "approve_drift" and candidate["drift_kind"] == "undeclared_edge":
            evidence = candidate.get("evidence", {})
            violations.append({**evidence, "violation_type": "intentional_drift",
                               "evidence_rule": "Human decision approved this undeclared dependency as intentional drift",
                               "human_decision": decision, "rule_confidence": 1.0})
        elif decision == "reject_drift" and candidate["drift_kind"] == "undeclared_edge":
            continue
        else:
            remaining.append(candidate)
    return {"raw_violations": violations, "drift_candidates": remaining, "warnings": warnings}

def _node_aggregate(state: AgentState):
    p = state["parsed"]
    report = aggregate(p.system_name, p.code_graph, p.architecture_graph, state["mapping"],
                       state["lifted_edges"], state["raw_violations"], state["drift_candidates"],
                       state.get("warnings"), state.get("errors"))
    return {"final_report": report}

def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("load_and_map", _node_load_and_map)
    graph.add_node("run_checkers", _node_run_checkers)
    graph.add_node("aggregate", _node_aggregate)
    graph.set_entry_point("load_and_map")
    graph.add_edge("load_and_map", "run_checkers")
    graph.add_edge("run_checkers", "aggregate")
    graph.add_edge("aggregate", END)
    return graph.compile()

app_graph = build_graph()

class ConsistencyCheckingAgent:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def run(self, input_payload: dict | ConsistencyInput) -> ConsistencyReport:
        payload = input_payload.model_dump() if isinstance(input_payload, ConsistencyInput) else input_payload
        result = await app_graph.ainvoke({"input_payload": payload})
        return result["final_report"]
