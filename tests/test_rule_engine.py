"""Kiểm thử Rule Engine và Graph Matching ở mức đơn vị."""
from __future__ import annotations

from consistency_agent.adapters.architecture_adapter import to_design_model
from consistency_agent.agent import ConsistencyCheckingAgent
from consistency_agent.core.naming import name_similarity
from consistency_agent.core.rule_engine import RuleEngine
from consistency_agent.schemas.inputs import RuleSet
from tests.helpers import by_type, code_envelope, design_graph, rules

NODES = [("ui", "UI"), ("app", "Application"), ("domain", "Domain"), ("infra", "Infrastructure")]
EDGES = [("ui", "app"), ("app", "domain"), ("infra", "domain")]


def engine(**kw) -> RuleEngine:
    rs = RuleSet.model_validate(kw)
    return RuleEngine(rs, to_design_model(design_graph(NODES, EDGES), rs))


def test_specific_allow_overrides_wildcard_forbid():
    e = engine(rules=[{"rule_id": "F", "kind": "forbid", "source": "*", "target": "infra"},
                      {"rule_id": "A", "kind": "allow", "source": "app", "target": "infra"}])
    assert e.evaluate("app", "infra").verdict == "allowed"
    assert e.evaluate("ui", "infra").verdict == "forbidden"


def test_conflicting_rules_are_undetermined_not_violation():
    e = engine(rules=[{"rule_id": "F", "kind": "forbid", "source": "ui", "target": "infra"},
                      {"rule_id": "A", "kind": "allow", "source": "ui", "target": "infra"}])
    v = e.evaluate("ui", "infra")
    assert v.verdict == "undetermined" and v.clarity < 0.7


def test_layer_rules_inward_and_strict():
    layers = [{"name": "presentation", "components": ["ui"]}, {"name": "application", "components": ["app"]},
              {"name": "domain", "components": ["domain"]}]
    e = engine(layers=layers)
    assert e.evaluate("domain", "ui").violation_type == "layer_violation"
    assert e.evaluate("ui", "domain").verdict != "forbidden"  # inward: được nhảy layer
    strict = engine(layers=layers, layer_policy="strict")
    assert strict.evaluate("ui", "domain").violation_type == "layer_violation"


def test_reverse_of_design_edge_and_default_policies():
    assert engine().evaluate("domain", "app").violation_type == "reverse_dependency"
    assert engine().evaluate("ui", "infra").verdict == "undetermined"  # chưa có luật -> không kết luận
    assert engine(default_policy="design_only").evaluate("ui", "infra").verdict == "forbidden"
    assert engine(default_policy="allow").evaluate("ui", "infra").verdict == "allowed"


def test_external_library_rule_and_require_rule():
    agent = ConsistencyCheckingAgent(knowledge=False)
    design = design_graph(NODES, EDGES)
    code = code_envelope({"ui.page": ["app.service"], "app.service": ["domain.entity"],
                          "domain.entity": ["sqlalchemy.orm"], "infra.repo": ["domain.entity"]})
    r = rules({"rule_id": "X1", "kind": "forbid", "source": "domain", "target": "external:sqlalchemy"},
              {"rule_id": "Q1", "kind": "require", "source": "app", "target": "infra", "severity": "medium"})
    report = agent.check(design, code, r)
    ext = [f for f in by_type(report, "forbidden_dependency") if f.rule_id == "X1"]
    assert len(ext) == 1 and ext[0].target_component == "external:sqlalchemy"
    req = [f for f in by_type(report, "missing_dependency") if f.rule_id == "Q1"]
    assert len(req) == 1 and req[0].severity == "medium"


def test_unclear_rule_yields_undetermined_status():
    agent = ConsistencyCheckingAgent(knowledge=False)
    code = code_envelope({"ui.page": ["app.service", "infra.repo"], "app.service": ["domain.entity"],
                          "domain.entity": [], "infra.repo": ["domain.entity"]})
    report = agent.check(design_graph(NODES, EDGES), code)
    f = next(x for x in report.findings if x.source_component == "ui" and x.target_component == "infra")
    assert f.status == "undetermined" and f.violation_type == "unexpected_dependency"
    assert report.statistics.undetermined_dependencies == 1


def test_name_similarity_tiers():
    assert name_similarity(["Use Cases"], "usecases")[1] == "exact"
    assert name_similarity(["Order Service"], "orders")[1] == "core"
    assert name_similarity(["Dependency Injection"], "di")[1] == "acronym"
    assert name_similarity(["Domain Entities"], "models")[1] == "synonym"
    s, m = name_similarity(["Payment Processor"], "paymnt_proc")
    assert m == "fuzzy" and s < 0.75
