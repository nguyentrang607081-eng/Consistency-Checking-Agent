# Testing and evaluation

Run the deterministic suite without an LLM key:

```bash
pip install -r requirements.txt
pytest -q
```

The labeled manifest is at `tests/data/labeled/ground_truth.json`. It specifies ten independent scenarios and expected label categories. The current manifest-validation test verifies coverage and vocabulary; it does not yet execute every manifest case through CCA. Report executable precision/recall/F1 only after graph payload fixtures and their expected edge-level identifiers have been independently reviewed and the evaluation test has actually been run.

For an evaluation set, compare predicted and expected violation identities using a stable key such as (violation_type, source_id, target_id, file_path, line_number). Compute TP, FP, FN, precision, recall, and F1 from those keys. Do not use scenario names alone as prediction identities.
