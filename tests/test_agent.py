import pytest
from consistency_checking_agent.agent import ConsistencyCheckingAgent

@pytest.mark.asyncio
async def test_detects_layer_skipping():
    payload = {
      "system_name":"Layered",
      "architecture_graph":{"nodes":[
        {"id":"presentation.Controller","name":"Controller","properties":{"layer":"presentation"}},
        {"id":"business.Service","name":"Service","properties":{"layer":"business"}},
        {"id":"data_access.Repo","name":"Repo","properties":{"layer":"data_access"}}],
        "edges":[{"id":"e1","source_id":"presentation.Controller","target_id":"business.Service","relationship_type":"sync_call"}]},
      "code_graph":{"nodes":[
        {"id":"src/presentation/controller.py","name":"Controller","file_path":"src/presentation/controller.py"},
        {"id":"src/data_access/repo.py","name":"Repo","file_path":"src/data_access/repo.py"}],
        "edges":[{"source_id":"src/presentation/controller.py","target_id":"src/data_access/repo.py","file_path":"src/presentation/controller.py","line_number":12,"code_snippet":"Repo()"}]}}
    report = await ConsistencyCheckingAgent().run(payload)
    assert any(v.violation_type == "layer_skipping" for v in report.violations)
    assert report.mapping_report.mapping_coverage == 1.0
