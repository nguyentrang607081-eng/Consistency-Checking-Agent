from consistency_checking_agent.schema import ArchitectureGraph, ArchitectureRules
from consistency_checking_agent.checkers.layer_checker import check_layers

def test_layer_skip_detected():
    graph = ArchitectureGraph(nodes=[
        {"id":"presentation.A","name":"A","properties":{"layer":"presentation"}},
        {"id":"business.B","name":"B","properties":{"layer":"business"}},
        {"id":"data_access.C","name":"C","properties":{"layer":"data_access"}}])
    rules = ArchitectureRules()
    edges = [{"source_id":"presentation.A","target_id":"data_access.C","mapping_score":1}]
    assert check_layers(edges, graph, rules)[0]["violation_type"] == "layer_skipping"

def test_allowed_edge_is_ignored():
    graph = ArchitectureGraph(nodes=[
        {"id":"presentation.A","name":"A","properties":{"layer":"presentation"}},
        {"id":"data_access.C","name":"C","properties":{"layer":"data_access"}}])
    rules = ArchitectureRules(allowed_edges=[{"source":"presentation.A","target":"data_access.C"}])
    assert check_layers([{"source_id":"presentation.A","target_id":"data_access.C"}], graph, rules) == []
