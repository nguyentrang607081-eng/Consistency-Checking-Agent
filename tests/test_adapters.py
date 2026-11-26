"""Kiểm thử lớp adapter (chuẩn hóa đầu ra AEA / SCA)."""
from __future__ import annotations

from consistency_agent.adapters.architecture_adapter import to_design_model
from consistency_agent.adapters.code_adapter import to_code_model
from consistency_agent.schemas.inputs import RuleSet
from tests.helpers import code_envelope, design_graph


def test_code_adapter_rebuilds_full_module_names_and_resolves_imports():
    env = code_envelope({
        "shop.api.views": [".schema", "..core.logic", "shop.core", "fastapi.responses", "src.ghost.thing"],
        "shop.api.schema": [],
        "shop.core.logic": ["pydantic"],
    })
    cm = to_code_model(env)
    assert {"shop.api.views", "shop.api.schema", "shop.core.logic", "shop", "shop.api"} <= set(cm.modules)
    res = {(d.evidence.raw_import, d.target, d.resolution) for d in cm.dependencies}
    assert (".schema", "shop.api.schema", "internal") in res
    assert ("..core.logic", "shop.core.logic", "internal") in res
    assert ("shop.core", "shop.core", "internal") in res
    assert ("fastapi.responses", "fastapi.responses", "external") in res
    assert ("src.ghost.thing", "ghost.thing", "unresolved_internal") in res
    assert cm.phantom_packages == {"ghost"}
    # SCA ghi from_module = tên file (views) -> adapter KHÔNG dùng, mà dựng lại từ file_path
    assert all(d.source_module.count(".") >= 1 for d in cm.dependencies)


def test_code_adapter_accepts_windows_paths_and_ignores_tests():
    env = {
        "project_id": "p", "run_id": "r", "status": "SUCCESS", "confidence": 0.95,
        "evidence": [r"C:\work\proj\app\__init__.py", r"C:\work\proj\app\main.py",
                     r"C:\work\proj\tests\__init__.py", r"C:\work\proj\tests\test_main.py"],
        "payload": {"dependencies": [
            {"from_module": "main", "to_module": "os", "dependency_type": "import",
             "evidence": {"file_path": r"C:\work\proj\app\main.py", "line_start": 1, "line_end": 1}},
            {"from_module": "test_main", "to_module": "app.main", "dependency_type": "import",
             "evidence": {"file_path": r"C:\work\proj\tests\test_main.py", "line_start": 1, "line_end": 1}},
        ]},
    }
    cm = to_code_model(env)
    assert "app.main" in cm.modules
    assert not any(m.startswith("tests") for m in cm.modules)
    assert len(cm.dependencies) == 1


def test_code_adapter_partial_status_adds_warning():
    cm = to_code_model(code_envelope({"a.b": []}, status="PARTIAL"))
    assert cm.status == "PARTIAL"
    assert any("PARTIAL" in w for w in cm.warnings)


def test_architecture_adapter_handles_aea_output():
    g = design_graph([("u", "User", "actor"), ("svc", "Service"), ("db", "Database", "database")],
                     [("u", "svc"), ("svc", "db"), ("svc", "ghost")])
    g["nodes"][1]["properties"] = {"layer": "application", "code_paths": ["src/service_pkg"]}
    dm = to_design_model({"final_graph": g})  # dạng bọc của state LangGraph AEA
    assert dm.components["u"].expected_in_code is False
    assert dm.components["db"].expected_in_code is False
    assert dm.components["svc"].layer == "application"
    assert dm.components["svc"].code_hints == ["src.service_pkg"]
    assert dm.graph.has_edge("svc", "db")
    assert any("ghost" in w for w in dm.warnings)


def test_architecture_adapter_database_expected_when_mapped():
    g = design_graph([("db", "Database", "database")], [])
    dm = to_design_model(g, RuleSet(component_mapping={"db": ["persistence"]}))
    assert dm.components["db"].expected_in_code is True
