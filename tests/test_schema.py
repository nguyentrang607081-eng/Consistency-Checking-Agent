import pytest
from pydantic import ValidationError
from consistency_checking_agent.schema import ConsistencyInput

def payload():
    return {"architecture_graph":{"nodes":[{"id":"business.A","name":"A"}],"edges":[]},
            "code_graph":{"nodes":[{"id":"a.py","name":"A","file_path":"a.py"}],"edges":[]}}

def test_valid_payload():
    assert ConsistencyInput.model_validate(payload()).architecture_graph.nodes[0].id == "business.A"

def test_rejects_invalid_confidence():
    data = payload()
    data["confidence_threshold"] = 1.5
    with pytest.raises(ValidationError):
        ConsistencyInput.model_validate(data)

def test_rejects_dangling_code_edge():
    data = payload()
    data["code_graph"]["edges"] = [{"source_id":"a.py","target_id":"missing"}]
    with pytest.raises(ValidationError):
        ConsistencyInput.model_validate(data)
