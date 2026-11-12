from consistency_checking_agent.schema import ArchitectureRules
from consistency_checking_agent.checkers.module_checker import check_cross_module

def test_cross_module_detected():
    rules = ArchitectureRules(modules=[
        {"name":"orders","members":["business.OrderService"]},
        {"name":"billing","members":["business.BillingService"]}])
    edges = [{"source_id":"business.OrderService","target_id":"business.BillingService"}]
    assert check_cross_module(edges, rules)[0]["violation_type"] == "cross_module"
