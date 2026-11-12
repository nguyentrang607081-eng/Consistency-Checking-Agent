from consistency_checking_agent.schema import ArchitectureGraph, CodeGraph
from consistency_checking_agent.element_mapper import map_code_to_architecture, lift_edges

def test_exact_and_unmapped_mapping():
    arch = ArchitectureGraph(nodes=[{"id":"business.OrderService","name":"OrderService"}])
    code = CodeGraph(nodes=[
        {"id":"src/order_service.py","name":"OrderService"},
        {"id":"src/unknown.py","name":"Unknown"}
    ], edges=[])
    result = map_code_to_architecture(code, arch)
    assert result[0].arch_node_id == "business.OrderService"
    assert result[0].method == "exact"
    assert result[1].arch_node_id is None

def test_edge_lifting_keeps_evidence():
    code = CodeGraph(nodes=[{"id":"a","name":"A"},{"id":"b","name":"B"}],
                     edges=[{"source_id":"a","target_id":"b","file_path":"a.py","line_number":8}])
    mapping = [
        {"code_node_id":"a","arch_node_id":"A","score":1.0},
        {"code_node_id":"b","arch_node_id":"B","score":.9}
    ]
    lifted = lift_edges(code, [__import__("consistency_checking_agent.schema",fromlist=["MappingEntry"]).MappingEntry(**x) for x in mapping])
    assert lifted[0]["line_number"] == 8
    assert lifted[0]["mapping_score"] == .9
