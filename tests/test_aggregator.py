from consistency_checking_agent.schema import ArchitectureGraph, CodeGraph, MappingEntry
from consistency_checking_agent.aggregator import aggregate

def test_empty_edge_score_is_safe():
    arch = ArchitectureGraph(nodes=[{"id":"A","name":"A"}])
    code = CodeGraph(nodes=[{"id":"a","name":"A"}], edges=[])
    report = aggregate("sample", code, arch,
                       [MappingEntry(code_node_id="a",arch_node_id="A",score=1,method="exact")],
                       [], [], [])
    assert report.consistency_score == 1.0
    assert report.mapping_report.mapping_coverage == 1.0
