# Kiến trúc Consistency Checking Agent (CCA)

## 1. Vị trí trong hệ thống 5 agent

Theo sơ đồ của nhóm (LangGraph Orchestration):

```
Tài liệu kiến trúc ─► Architecture Extraction Agent ──(ArchitectureGraph)──┐
Mã nguồn ─────────► Source Code Analysis Agent ────(SourceCodeEnvelope)───┼─► Consistency Checking Agent ─► Coordinator Agent ─► Reporting Agent ─► Báo cáo
Rule ─────────────────────────────────────────────────(RuleSet)───────────┘          │      ▲                    │
                                                                                     ▼      │ chỉ thị chiến lược │
                                                         Kho tri thức (raw metadata của CCA) └────────────────────┘
```

* CCA nhận **2 graph + rule**, đúng như bảng "Quá trình Agent xử lý Input" trong Demo tuần 2.
* CCA **ghi raw metadata** (ánh xạ, lịch sử quét, quyết định người dùng) vào kho tri thức riêng
  (SQLite). Tầng *unified findings* vẫn do Coordinator ghi — đúng mũi tên trong sơ đồ.
* Đầu ra `ConsistencyReport` có đủ dữ liệu cho Reporting Agent (finding, đồ thị so sánh, drift,
  thống kê, metadata hiệu năng). Đồng thời có adapter sinh `CoordinatorInput` vì trong sơ đồ
  luồng đi qua Coordinator trước khi tới Reporting.

## 2. Kết quả khảo sát repository của nhóm (26/11/2026)

Cả 4 repo đều clone được và mình đã đọc mã nguồn thực tế.

| Repo | Ngôn ngữ / framework | Đầu ra | Trạng thái kiểm tra |
|---|---|---|---|
| Architecture-Extraction-Agent | Python, LangGraph, FastAPI, Groq LLM, rapidfuzz | `ArchitectureGraph{nodes[id,name,type,c4_level,properties,source,confidence,evidence], edges[id,source_id,target_id,relationship_type,label,source,confidence], warnings}` | **Chưa chạy được ở đây**: cần `GROQ_API_KEY`; repo chưa có `requirements.txt`/README. Parser PlantUML thuần regex. |
| source_code_analysis_agent | Python, LangGraph, tree-sitter | `SourceCodeEnvelope{status, confidence, evidence[files], payload.dependencies[from_module,to_module,dependency_type,evidence{file_path,line_start,line_end}]}` | **Chưa chạy được ở đây**: môi trường không cài được `tree-sitter`. Đã đọc code + dùng bộ dữ liệu test `py-clean-arch` (bản gốc và bản cấy lỗi) của repo. |
| coordinator-agent | Python, Pydantic, SQLite, LangGraph (có fallback) | `ReportingPayload`; nhận `{context, consistency_result{drift_score, findings[]}}` | **Chạy được**: 8/8 test pass. Đã tích hợp thật (scripts/integration_check.py). |
| Adaptive-Recommendation-Agent | Python, Pydantic, LangGraph (có fallback), FastAPI | Nhận `RecommendationRequest` (issues COMPLIANT/VIOLATION/REVIEW) | **Chạy được**: 5/5 test pass. Đã tích hợp thật. |

### Những điểm dữ liệu thực tế ảnh hưởng tới thiết kế CCA

1. **SCA đặt `from_module` = tên file** (`Path.stem`): `controllers/rest/pokemon/mapper.py` và
   `repositories/document_db/pokemon/mapper.py` đều thành `"mapper"`. → CCA **không dùng** `from_module`
   mà dựng lại tên module đầy đủ từ `evidence.file_path` (theo quy tắc `__init__.py` của Python).
2. **SCA giữ chuỗi import thô**: `.mapper`, `..base`, `...abstraction`, `src.infrastructure.x`.
   → CCA tự phân giải import tương đối và phát hiện import tới package không tồn tại ("package ma").
3. SCA hiện bỏ sót `import x as y` (node `aliased_import` của tree-sitter không được duyệt) và chưa
   hiện thực `incremental` (roadmap). CCA không phụ thuộc hai điểm này.
4. **AEA không xuất layer, rule hay ánh xạ tới code**. `properties` là dict tự do. → CCA đọc tùy chọn
   `properties.layer` và `properties.code_paths` nếu AEA bổ sung sau; còn lại tự ánh xạ bằng Graph Matching.
5. AEA gán `type` ∈ {service, database, ui, queue, cache, external_system, actor} và `confidence`
   (node lấy từ văn bản qua LLM thường < 1). → CCA không kỳ vọng `actor/external_system/database/queue/cache`
   có package trong code (trừ khi được khai báo ánh xạ) và **đưa confidence của AEA vào confidence finding**.
6. **Coordinator chỉ nhận 4 loại finding** (`forbidden_dependency, missing_dependency, divergence, absence`)
   và 3 nguồn (`rule_engine, graph_matching, similarity`) → CCA có adapter chuyển đổi, giữ loại gốc trong `evidence`.
7. Coordinator sinh sẵn chỉ thị cho CCA: `build_for("consistency_checking") = {confidence_threshold, target_modules}`
   → CCA nhận đúng định dạng này làm `StrategyDirective`.
8. Hai repo Coordinator/Recommendation dùng mẫu "LangGraph nếu có, không thì chạy tuần tự" → CCA dùng cùng mẫu.

## 3. Các module

```
consistency_agent/
├── schemas/
│   ├── inputs.py       # Hợp đồng vào: ArchitectureGraphIn (AEA), SourceCodeEnvelopeIn (SCA), RuleSet, StrategyDirective
│   ├── internal.py     # Mô hình nội bộ: DesignModel, CodeModel (NetworkX)
│   └── outputs.py      # Hợp đồng ra: ConsistencyReport, Finding, ComponentMapping, DriftMetrics, ...
├── adapters/
│   ├── architecture_adapter.py   # AEA -> DesignModel
│   └── code_adapter.py           # SCA -> CodeModel (dựng tên module, phân giải import)
├── core/
│   ├── naming.py         # điểm tên: exact / core / head / acronym / synonym / fuzzy
│   ├── graph_matching.py # ánh xạ component <-> package (tên + cấu trúc, lặp, neo cứng/mềm)
│   ├── rule_engine.py    # luật tường minh, layer, suy ra từ thiết kế, chính sách mặc định
│   ├── checker.py        # Reflexion Model: convergence/divergence/absence + sinh finding
│   ├── confidence.py     # công thức confidence
│   └── drift.py          # similarity / drift / violation index
├── adaptive/
│   ├── mape_k.py         # Monitor, Analyze, Plan (luật P1..P7)
│   └── knowledge.py      # Knowledge: SQLite
├── integration/
│   ├── coordinator.py      # -> CoordinatorInput; <- chỉ thị chiến lược
│   ├── recommendation.py   # -> RecommendationRequest (Adaptive Recommendation Agent)
│   └── langgraph_node.py   # node cho workflow 5 agent
├── scenarios.py            # 4 kịch bản: match / wrong / missing / junk (+ uncertain)
├── rag/                    # retriever.py (BM25), fewshot.py, corpus/*.md
├── llm/providers.py        # reviewer: LLM tương thích OpenAI (groq/ollama/openai) hoặc local-fewshot
├── review.py               # RAG + few-shot + LLM review (cache, ngân sách, policy)
├── tools/sca_fallback.py   # sinh envelope định dạng SCA bằng `ast` (khi chưa có tree-sitter)
├── agent.py                # Facade + workflow normalize→monitor→analyze→plan→execute→knowledge→package
└── __main__.py             # CLI
```

## 4. Luồng xử lý

```
normalize : validate + adapter  (AEA graph, SCA envelope, RuleSet, chỉ thị Coordinator)
monitor   : kích thước graph, chất lượng dữ liệu, chữ ký package, baseline từ Knowledge
analyze   : nhỏ/lớn, package nào đổi, thiết kế có đổi, tỷ lệ tên mơ hồ, chất lượng dữ liệu
plan      : chọn scope (full/incremental), vùng tập trung, deep matching, ngưỡng tin cậy (P1..P7)
execute   : Graph Matching → nâng phụ thuộc module lên component → Rule Engine → absence
            → component thiếu/thừa → chu trình → quyết định người dùng → carry-over → drift
review    : xếp 4 kịch bản; RAG + few-shot + LLM rà soát finding (xem SCENARIOS_RAG_LLM.md)
knowledge : lưu ánh xạ tự động, chữ ký package, cạnh thực tế, finding, drift
package   : ConsistencyReport (JSON)
```

## 5. Graph Matching (Reflexion Model – bước mapping)

Demo tuần 2 chọn Reflexion Model, nhưng hạn chế của công cụ cũ là **người dùng phải tự viết ánh xạ**.
CCA giữ ánh xạ khai báo làm "neo" (nếu có) và **tự đề xuất** phần còn lại:

* Điểm tên `name(D,P)` (xem `naming.py`), phạt nhẹ package sâu.
* Điểm cấu trúc `struct(D,P)` = tỷ lệ cạnh thiết kế của D (tới các component đã neo) có phụ thuộc
  cùng chiều trong code giữa P và package của component kia.
* Kết hợp noisy-OR: `conf = 1 − (1 − 0.85·name)(1 − 0.70·struct)`.
* Lặp tối đa 3 vòng (4 khi deep): cặp chắc chắn thành **neo cứng**, cặp chưa chắc thành **neo mềm**
  (trọng số = confidence) → bằng chứng cấu trúc lan dần (similarity propagation rút gọn).
* Gán tham lam 1-1; nếu ứng viên thứ hai cách < 0.08 → **mơ hồ** → `uncertain`.

Vì sao không bao giờ kết luận "giống nhau" chỉ vì tên gần giống: tên gần giống tối đa
0.85×0.8 = 0.68 < ngưỡng 0.75 nếu không có cấu trúc; chỉ có cấu trúc tối đa 0.70. Cả hai trường hợp
đều ra `uncertain` + finding `uncertain_mapping` cần người xác nhận (test 6, 6b, 6c).

## 6. Rule Engine

Thứ tự ưu tiên khi đánh giá một phụ thuộc thực tế S→T:
luật tường minh (cụ thể nhất thắng; allow/forbid ngang nhau → `undetermined`) → luật layer →
luật suy ra từ thiết kế (cạnh thiết kế = cho phép; cạnh ngược = `reverse_dependency`) →
chính sách mặc định (`design_only` / `unspecified` / `allow`).
Luật với thư viện ngoài: `target: "external:sqlalchemy"`. Luật bắt buộc: `kind: "require"`.

Khi không có luật nào quyết định (`unspecified`), CCA ghi `status = "undetermined"` thay vì
suy diễn thành vi phạm.

## 7. Quyết định kỹ thuật

| Quyết định | Lý do |
|---|---|
| Python + Pydantic v2 + NetworkX | Trùng stack của cả 4 repo; NetworkX đúng gợi ý Demo tuần 2 (SQLite + NetworkX). |
| LangGraph **tùy chọn** | Giống Coordinator/Recommendation: chạy được offline trên Windows khi chưa cài LangGraph. |
| LLM chỉ là ý kiến thứ hai, mặc định offline | Không bắt buộc API trả phí; confidence vẫn tính bằng công thức giải thích được; LLM (Groq/Ollama/OpenAI) bật khi cần, có ngân sách + cache; `performance.llm_calls` để so sánh chi phí. |
| Adapter tách khỏi lõi | AEA/SCA còn đang phát triển; đổi schema chỉ cần sửa adapter. |
| SQLite cho Knowledge | Không cần server, chạy local Windows, đúng hướng "SQLite + NetworkX" của nhóm. |
| Fingerprint ổn định (sha1 của loại + nguồn + đích + luật) | Nhớ quyết định của người dùng qua các lần quét. |
