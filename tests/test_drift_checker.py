from consistency_checking_agent.schema import ArchitectureGraph, ArchitectureRules, MappingEntry
from consistency_checking_agent.checkers.drift_checker import check_drift

def test_undeclared_and_missing_edges_are_candidates():
    arch = ArchitectureGraph(nodes=[{"id":"A","name":"A"},{"id":"B","name":"B"},{"id":"C","name":"C"}],
                             edges=[{"source_id":"A","target_id":"B"}])
    lifted = [{"source_id":"A","target_id":"C","mapping_score":.9}]
    _, candidates = check_drift(lifted, arch, ArchitectureRules(), [])
    kinds = {x["drift_kind"] for x in candidates}
    assert kinds == {"undeclared_edge","missing_edge"}
