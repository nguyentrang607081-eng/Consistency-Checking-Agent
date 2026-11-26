# Hướng dẫn tích hợp với 4 agent còn lại

## 1. Bốn interface

| Hướng | Interface | Định dạng |
|---|---|---|
| AEA → CCA | tham số `architecture_graph` | `ArchitectureGraph` của AEA (dict JSON hoặc model Pydantic; chấp nhận dạng bọc `{"final_graph": {...}}`) |
| SCA → CCA | tham số `code_envelope` | `SourceCodeEnvelope` của SCA (đường dẫn tuyệt đối/tương đối, Windows/Linux đều được) |
| Coordinator / người dùng → CCA | `rules` (RuleSet), `strategy` (StrategyDirective), `context` | xem mục 3 |
| CCA → Reporting | giá trị trả về `ConsistencyReport` | `report.model_dump(mode="json")` |
| CCA → Coordinator | `to_coordinator_input(report)` | đúng `CoordinatorInput` của repo coordinator-agent |
| CCA → Adaptive Recommendation | `to_recommendation_request(report)` | đúng `RecommendationRequest` (schema `extra="forbid"`) |

### Python API

```python
from consistency_agent import ConsistencyCheckingAgent
from consistency_agent.integration.coordinator import to_coordinator_input

agent = ConsistencyCheckingAgent(knowledge="data/cca_knowledge.sqlite")   # hoặc knowledge=False
report = agent.check(architecture_graph=aea_graph,      # output AEA
                     code_envelope=sca_envelope,        # output SCA
                     rules=rule_set,                    # tùy chọn
                     strategy=coordinator_directive,    # tùy chọn
                     context={"project_id": "p1", "scan_id": "scan-42", "commit_hash": "a1b2c3"})
reporting_input = report.model_dump(mode="json")        # -> Reporting Agent
coordinator_input = to_coordinator_input(report)        # -> Coordinator Agent

# hoặc kiểu async giống các agent khác của nhóm:
result_dict = await agent.run({"architecture_graph": ..., "code_envelope": ..., "rules": ..., "strategy": ..., "context": ...})
```

### Node LangGraph cho workflow 5 agent

```python
from consistency_agent.integration.langgraph_node import make_node
graph.add_node("consistency_checking", make_node(kb_path="data/cca_knowledge.sqlite"))
graph.add_edge("code_analysis", "consistency_checking")
graph.add_edge("consistency_checking", "coordinator")
```
State đọc: `architecture_graph`, `code_envelope`, `rules`, `cca_strategy`, `context`.
State ghi: `consistency_report`, `coordinator_input`.

## 2. Vòng điều phối Coordinator ↔ CCA (đã chạy thật)

`scripts/integration_check.py` chạy CCA → `CoordinatorAgent.run()` của repo coordinator-agent →
lấy `downstream_payloads["consistency_checking"]` → chạy lại CCA với chỉ thị đó.

```bash
python scripts/integration_check.py --coordinator ../coordinator-agent --recommendation ../Adaptive-Recommendation-Agent
```

Kết quả thực tế (26/11/2026): Coordinator nhận đủ finding của 2 lần quét, phân loại delta
`new=4, persistent=1`, không có finding nào bị loại vì sai schema; CCA nhận chỉ thị
`{"confidence_threshold": 0.7, "target_modules": []}` và ghi nguồn chiến lược `agent+coordinator`.
Adaptive Recommendation Agent validate thành công `RecommendationRequest` (5 issue) và sinh 5 đề xuất.

## 3. Định dạng RuleSet (do CCA định nghĩa)

Chưa agent nào xuất rule, nên CCA định nghĩa định dạng sau (file `examples/*/rules.json`):

```json
{
  "default_policy": "unspecified",          // design_only | unspecified | allow
  "layers": [                                // từ NGOÀI vào TRONG
    {"name": "presentation", "components": ["controllers"]},
    {"name": "application",  "components": ["usecases"]},
    {"name": "domain",       "components": ["entities"]}
  ],
  "layer_policy": "inward",                 // inward | strict
  "component_mapping": {"entities": ["models"]},   // tùy chọn: ánh xạ khai báo
  "derive_rules_from_design": true,
  "ignore_code_packages": ["main"],
  "rules": [
    {"rule_id": "R1", "kind": "forbid",  "source": "controllers",  "target": "repositories", "severity": "high", "description": "..."},
    {"rule_id": "R2", "kind": "forbid",  "source": "layer:domain", "target": "external:sqlalchemy"},
    {"rule_id": "R3", "kind": "allow",   "source": "usecases",     "target": "dependency_injection"},
    {"rule_id": "R4", "kind": "require", "source": "controllers",  "target": "usecases"}
  ]
}
```

Selector: id component thiết kế · tên package code (vd `"common"`) · `layer:<tên>` · `external:<thư viện>` · `*`.

## 4. StrategyDirective (Coordinator → CCA)

Nhận nguyên `build_for("consistency_checking", strategy)` của Coordinator, có thể thêm trường:

```json
{"scope": "incremental", "target_modules": ["controllers"], "confidence_threshold": 0.8, "deep_matching": true}
```

## 5. Ví dụ đầu ra

Báo cáo đầy đủ: `examples/sample_outputs/*.report.json`. Các phần chính:

```json
{
  "schema_version": "1.0.0", "agent_name": "Consistency Checking Agent",
  "project_id": "ecommerce-demo", "scan_id": "scan-ecommerce-1", "status": "SUCCESS",
  "strategy": {"scope": "full", "focus_components": [], "matching_rounds": 3, "deep_matching": false,
               "confidence_threshold": 0.7, "reasons": ["P2: chưa có lần quét trước trong Knowledge -> quét toàn bộ."]},
  "adaptation_trace": [{"phase": "monitor", "detail": {}}, "... analyze, plan, execute, knowledge"],
  "mappings": [{"design_id": "api_gateway", "code_packages": ["gateway"], "confidence": 0.9244,
                "status": "confirmed", "method": "core+structure", "name_score": 0.88, "structure_score": 1.0,
                "alternatives": [{"code_package": "notify", "score": 0.469}]}],
  "dependencies": [{"source": "web_app", "target": "order_service", "reflexion": "divergence",
                    "verdict": "forbidden", "rule_id": "R1", "occurrences": 1, "evidence": []}],
  "findings": [{
    "finding_id": "CCA-0001", "fingerprint": "6a1b445598e77e0b",
    "violation_type": "forbidden_dependency", "severity": "high",
    "confidence_score": 0.8963,
    "confidence_breakdown": {"mapping": 0.8963, "rule_clarity": 1.0, "evidence": 1.0, "data_completeness": 1.0},
    "status": "confirmed", "detected_by": "rule_engine", "rule_id": "R1",
    "description": "Web App không được gọi thẳng Order Service; mọi request phải đi qua API Gateway. (1 lần import)",
    "source_component": "web_app", "target_component": "order_service",
    "expected_dependency": "web_app -/-> order_service (bị cấm)", "actual_dependency": "web_app -> order_service",
    "file_path": "webapp/views.py", "line_number": 3,
    "evidence": [{"file_path": "webapp/views.py", "line_start": 3, "line_end": 3,
                  "statement": "import orders.repository -> orders.repository (internal)"}],
    "recommended_action": "Loại bỏ phụ thuộc trực tiếp web_app -> order_service; ...",
    "carried_over": false}],
  "drift": {"structural_similarity": 0.6364, "drift_score": 0.3636, "drift_level": "moderate",
            "dependency_conformance": 0.5, "dependency_realization": 1.0, "violation_index": 0.1174,
            "changed_relations": {"added_dependencies": ["web_app -> order_service", "..."], "added_components": ["code:analytics"]}},
  "statistics": {"total_findings": 3, "open_violations": 3, "by_status": {"confirmed": 2, "needs_review": 1}},
  "visualization": {"nodes": [{"id": "web_app", "label": "Web App", "kind": "design", "status": "matched"}],
                    "edges": [{"source": "api_gateway", "target": "order_service", "reflexion": "convergence", "verdict": "allowed", "weight": 1}]},
  "performance": {"total_ms": 5.3, "phase_ms": {"graph_matching": 1.2}, "llm_calls": 0}
}
```

Đầu ra cho Coordinator (`examples/sample_outputs/py_clean_arch_faulty.coordinator_input.json`):

```json
{
  "context": {"project_id": "py-clean-arch", "scan_id": "scan-2-faulty", "scan_scope": "full",
              "scanned_modules": [], "baseline_scan_id": "scan-1-clean", "commit_hash": ""},
  "consistency_result": {
    "drift_score": 0.2174,
    "findings": [{"id": "CCA-0001", "rule_id": "DESIGN:component-set", "type": "divergence", "severity": "high",
                  "source_component": "domain", "target_component": "-", "file": "src/models/pokemon.py", "line": 8,
                  "evidence": "[unexpected_component|confirmed] Package 'domain' được import nhưng KHÔNG tồn tại ...",
                  "confidence": 0.81, "source": "graph_matching"}],
    "warnings": [], "errors": []}
}
```

### Trường mới (4 kịch bản + review)

Mỗi finding có thêm `scenario` (`match|wrong|missing|junk|uncertain`), `scenario_detail` và `llm_review`
(`provider, model, is_llm, verdict, scenario, confidence, rationale, suggested_fix, retrieved_docs, cited_docs,
few_shot_ids, cached`). Báo cáo có thêm `scenario_report` và `review` (thống kê review). Trong `CoordinatorInput`,
kịch bản được ghi trong chuỗi `evidence`: `[loại|kịch bản|status] ...`. `StrategyDirective` nhận thêm `llm_review`;
`directive_from_coordinator(payload, notes)` tự bật khi ghi chú của Coordinator đề nghị kiểm tra lại bằng LLM.

## 6. Đề xuất thay đổi cho agent khác (không bắt buộc)

CCA **không yêu cầu** agent khác sửa gì để chạy được. Các đề xuất sau giúp kết quả tốt hơn:

| Agent | Đề xuất | Lý do |
|---|---|---|
| SCA | Đặt `from_module` = tên module đầy đủ (vd `controllers.rest.pokemon.mapper`) thay vì `Path.stem` | Tránh trùng tên; CCA hiện phải tự dựng lại từ `file_path`. |
| SCA | Duyệt thêm node `aliased_import` (`import x as y`) | Đang bỏ sót loại import này. |
| AEA | Ghi `properties.layer` và (nếu biết) `properties.code_paths` cho node | CCA dùng trực tiếp làm luật layer và ánh xạ khai báo (neo chắc chắn). |
| AEA | Xuất danh sách rule trích từ tài liệu theo định dạng RuleSet | Hiện thực ý "tự suy luật từ tài liệu kiến trúc" của Demo tuần 2. |
| Coordinator | Mở rộng `FindingType` thêm `layer_violation`, `cyclic_dependency`, `unexpected_component` | Hiện CCA phải gộp về 4 loại; loại gốc đang nằm trong chuỗi `evidence`. |
