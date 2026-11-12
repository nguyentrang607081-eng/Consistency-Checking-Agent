# Labeled evaluation dataset

`ground_truth.json` defines ten graph-level scenarios and expected labels **before** running the CCA. The manifest is not generated from CCA output.

- CCA-001: consistent project
- CCA-002: layer skipping
- CCA-003: back-call
- CCA-004: cross-module
- CCA-005: circular dependency
- CCA-006: undeclared edge
- CCA-007: missing edge
- CCA-008: unmapped node
- CCA-009: combined violations
- CCA-010: ambiguous mapping

The manifest is a label specification, not a claim that all ten executable project fixtures have already been run. Extend `tests/test_labeled_dataset.py` with graph payloads as the CodeGraph contract is finalized with the Source Code Analysis Agent.
