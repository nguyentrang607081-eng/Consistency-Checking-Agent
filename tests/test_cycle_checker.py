from consistency_checking_agent.checkers.cycle_checker import check_cycles

def test_cycle_detected_once_per_scc():
    edges = [{"source_id":"A","target_id":"B"},{"source_id":"B","target_id":"A"}]
    result = check_cycles(edges)
    assert len(result) == 1
    assert result[0]["violation_type"] == "circular_dependency"
