# Assumptions and unresolved decisions

1. **CodeGraph contract:** the technical specification marks it as pending agreement with the Source Code Analysis owner. The current Pydantic adapter follows the example fields: id/name/type/file_path/language/properties for nodes and source_id/target_id/relationship_type/file_path/line_number/code_snippet for edges. Extra fields are preserved.
2. **Architecture layers:** resolved in this order: explicit rules.node_layers, node.properties.layer, then ID prefix if it matches configured rules.layers.
3. **Modules:** module membership must be explicitly provided using rules.node_modules or rules.modules. No inferred module boundary is asserted.
4. **LLM:** disabled by default. The deterministic pipeline does not depend on an API key. The cache/classifier adapter requires a provider client implementing async classify_drift(candidate); provider integration is not enabled in the default API.
5. **Cross-module rule:** a cross-module dependency is reported only when both endpoint modules are known and differ, and the edge is not allowed. Confirm whether design graph declaration should also be used to suppress it.
6. **Scoring:** confidence uses min endpoint mapping score × rule confidence (1.0 for layer/cycle, 0.9 for cross-module). Consistency score is valid lifted edges / all lifted edges. A zero denominator returns 1.0, with mapping warnings where applicable.
7. **Cycle evidence:** one representative internal edge is attached to one violation per strongly connected component; the SCC members are listed in evidence_rule.
8. **Drift:** undeclared and missing edges remain candidates. A human approve_drift decision turns an undeclared edge into intentional_drift; reject_drift removes that candidate.
9. **No false claim:** deterministic output means repeatable rule execution, not guaranteed 100% real-world detection accuracy.
