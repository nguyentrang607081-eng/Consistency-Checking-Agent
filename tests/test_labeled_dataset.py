import json
from pathlib import Path

DATASET = Path(__file__).parent / "data" / "labeled" / "ground_truth.json"
ALLOWED_VIOLATIONS = {
    "layer_skipping", "back_call", "cross_module",
    "circular_dependency", "intentional_drift",
}
ALLOWED_DRIFTS = {"undeclared_edge", "missing_edge", "unmapped_node"}


def test_ground_truth_manifest_has_ten_required_scenarios():
    manifest = json.loads(DATASET.read_text(encoding="utf-8"))
    cases = manifest["cases"]
    assert len(cases) >= 10
    assert len({case["id"] for case in cases}) == len(cases)
    required = {
        "consistent-layered-project", "layer-skipping", "back-call",
        "cross-module", "circular-dependency", "undeclared-edge",
        "missing-edge", "unmapped-node", "combined-violations",
        "ambiguous-mapping",
    }
    assert required <= {case["name"] for case in cases}


def test_ground_truth_labels_use_contract_vocabulary():
    manifest = json.loads(DATASET.read_text(encoding="utf-8"))
    for case in manifest["cases"]:
        assert set(case["expected_violations"]) <= ALLOWED_VIOLATIONS
        assert set(case["expected_drift_candidates"]) <= ALLOWED_DRIFTS
        assert case["description"].strip()
