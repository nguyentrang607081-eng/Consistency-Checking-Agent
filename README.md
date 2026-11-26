# Consistency Checking Agent (CCA)

Agent kiểm tra **tính nhất quán giữa kiến trúc thiết kế và mã nguồn** trong hệ thống Adaptive
Multi-Agent của đề tài *"Nghiên cứu Adaptive Multi-Agent nhằm đảm bảo tính nhất quán giữa kiến trúc
phần mềm và mã nguồn"*.

Theo Demo tuần 2:

| Input | Processing | Output |
|---|---|---|
| 2 graph (kiến trúc thiết kế từ **Architecture Extraction Agent**, kiến trúc thực tế từ **Source Code Analysis Agent**) + rule | Graph Matching, Rule Engine, Similarity (Reflexion Model) + vòng **MAPE-K** | Vi phạm kiến trúc (có confidence + bằng chứng file/dòng), architecture drift, dữ liệu cho Reporting/Coordinator |

Kết quả được xếp vào **4 kịch bản**: (1) cặp dữ liệu khớp hoàn toàn, (2) code sai so với thiết kế,
(3) code thiếu so với thiết kế, (4) code rác – thiết kế không có. Mỗi finding được rà soát thêm bằng
**RAG + few-shot + LLM** (ý kiến thứ hai, có trích dẫn tài liệu).

Mặc định agent **không cần API key**: bước review chạy bằng RAG + few-shot cục bộ (offline). Muốn dùng LLM thật:
Groq (cùng key với Architecture Extraction Agent), Ollama (miễn phí, chạy trên máy) hoặc API tương thích OpenAI.
Chỉ cần `pydantic` và `networkx`; chạy trên Windows/macOS/Linux.

---

## 1. Cài đặt

Yêu cầu: **Python 3.10+** (đã kiểm thử trên Python 3.13), Git.

### Windows (PowerShell)

```powershell
git clone <URL-repo-này> consistency-checking-agent
cd consistency-checking-agent
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Nếu PowerShell chặn script: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` rồi kích hoạt lại.

### macOS / Linux

```bash
git clone <URL-repo-này> consistency-checking-agent
cd consistency-checking-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Tùy chọn: `pip install langgraph` để workflow nội bộ chạy bằng LangGraph `StateGraph`
(không cài thì agent chạy tuần tự cùng thứ tự node — giống repo Coordinator của nhóm).

## 2. Chạy nhanh

```bash
# Demo 4 kịch bản (khớp / code sai / code thiếu / code rác) + bảng tổng hợp
python -m consistency_agent scenarios
python -m consistency_agent scenarios -v      # in thêm nhận xét RAG + few-shot cho từng finding

# 3 kịch bản demo trên dữ liệu thật của nhóm (py-clean-arch của SCA, e-commerce của AEA)
python -m consistency_agent demo

# Chạy bộ test
python -m pytest -v

# Xem kết quả JSON
#   Windows : type outputs\ecommerce.report.json
#   Linux   : cat outputs/ecommerce.report.json
```

### Kiểm tra dữ liệu của bạn

```bash
python -m consistency_agent check \
  --design examples/ecommerce/design_architecture.json \
  --code   examples/ecommerce/code_envelope.json \
  --rules  examples/ecommerce/rules.json \
  -o outputs/report.json \
  --coordinator-output outputs/coordinator_input.json \
  --recommendation-output outputs/recommendation_request.json
```

(Trên PowerShell viết trên một dòng, hoặc thay `\` bằng dấu backtick `` ` ``.)

| Tham số | Ý nghĩa |
|---|---|
| `--design` | JSON `ArchitectureGraph` — đầu ra Architecture Extraction Agent |
| `--code` | JSON `SourceCodeEnvelope` — đầu ra Source Code Analysis Agent |
| `--rules` | JSON `RuleSet` (tùy chọn) — xem `docs/INTEGRATION.md` |
| `--strategy` | JSON chỉ thị chiến lược từ Coordinator (tùy chọn) |
| `--project-id`, `--scan-id`, `--commit` | Định danh lần quét (Knowledge dùng để so sánh các lần) |
| `--threshold` | Ngưỡng confidence (mặc định 0.7) |
| `--kb` / `--no-kb` | Đường dẫn SQLite Knowledge (mặc định `data/cca_knowledge.sqlite`) / tắt Knowledge |

### Bật LLM (tùy chọn)

```powershell
# Groq - cùng key với Architecture Extraction Agent
$env:GROQ_API_KEY = "gsk_..."
python -m consistency_agent scenarios --llm-provider groq -v

# Ollama - miễn phí, chạy trên máy: cài Ollama rồi
ollama pull qwen2.5:7b-instruct
python -m consistency_agent scenarios --llm-provider ollama -v
```

`--llm-policy resolve` cho phép LLM nâng finding "cần xác nhận" thành "confirmed" (không bao giờ tự gỡ finding).
Chi tiết: **[docs/SCENARIOS_RAG_LLM.md](docs/SCENARIOS_RAG_LLM.md)**.

### Chưa có Source Code Analysis Agent? Tạo envelope từ thư mục code

SCA thật cần `tree-sitter`. Để thử nhanh, CCA có bộ trích xuất dự phòng dùng `ast` của Python,
sinh JSON **đúng định dạng SCA**:

```bash
python -m consistency_agent extract-code path/to/your/project -o outputs/code.json
python -m consistency_agent check --design my_design.json --code outputs/code.json -o outputs/report.json
```

### Human-in-the-loop: phản hồi để lần quét sau thông minh hơn

```bash
# chấp nhận một vi phạm (fingerprint lấy trong report.json)
python -m consistency_agent feedback accept --project ecommerce-demo --fingerprint 6a1b445598e77e0b --note "Tạm chấp nhận"
# đánh dấu báo nhầm
python -m consistency_agent feedback false-positive --project ecommerce-demo --fingerprint <fp>
# xác nhận / bác bỏ một ánh xạ component
python -m consistency_agent feedback confirm-mapping --project p1 --design-id payment --packages paymnt_proc
python -m consistency_agent feedback reject-mapping  --project p1 --design-id payment --packages pay_utils
```

## 3. Agent làm gì?

```
normalize ─► monitor ─► analyze ─► plan ─► execute ─► review ─► knowledge ─► package
 (adapter)   └────────────── MAPE-K ──────────────────────┘   │
                                               ├─ (review) 4 kịch bản + RAG (BM25) + few-shot + LLM/local reviewer
                                               ├─ Graph Matching : ánh xạ component thiết kế ↔ package code (tên + cấu trúc)
                                               ├─ Reflexion Model: convergence / divergence / absence
                                               ├─ Rule Engine    : luật forbid/allow/require, layer, suy ra từ thiết kế
                                               ├─ Component thiếu / thừa / "package ma" / chu trình
                                               └─ Similarity & Drift + confidence từng finding
```

Các loại vi phạm phát hiện được:

| `violation_type` | Ý nghĩa |
|---|---|
| `forbidden_dependency` | Vi phạm luật cấm (kể cả cấm thư viện ngoài, vd domain → sqlalchemy) |
| `layer_violation` | Layer trong phụ thuộc ngược lên layer ngoài |
| `reverse_dependency` | Phụ thuộc ngược chiều so với thiết kế |
| `cyclic_dependency` | Chu trình phụ thuộc giữa các component (dấu hiệu erosion) |
| `unexpected_dependency` | Phụ thuộc có trong code nhưng không có trong thiết kế (divergence) |
| `missing_dependency` | Phụ thuộc thiết kế/luật `require` không thấy trong code (absence) |
| `missing_component` | Component thiết kế không tìm thấy trong code |
| `unexpected_component` | Package trong code không có trong kiến trúc, hoặc import tới package không tồn tại |
| `uncertain_mapping` | Ánh xạ chưa chắc chắn → cần người xác nhận |

Mỗi finding có `confidence_score = mapping × rule_clarity × evidence × data_completeness` và
`status`: `confirmed` / `needs_review` / `undetermined` (luật không đủ rõ) / `accepted`.
Công thức chi tiết: **[docs/METRICS.md](docs/METRICS.md)**.

### Cơ chế Adaptive (MAPE-K)

| Pha | Việc làm thực tế |
|---|---|
| Monitor | Đếm node/cạnh, import chưa phân giải, trạng thái SCA, độ tin cậy node AEA; tính chữ ký từng package; đọc lần quét trước từ Knowledge |
| Analyze | Đồ thị nhỏ/lớn, package nào thay đổi, tài liệu kiến trúc có đổi, tỷ lệ tên mơ hồ, chất lượng dữ liệu |
| Plan | Luật P1–P7: tuân theo Coordinator; full khi chưa có baseline/thiết kế đổi/đồ thị nhỏ; **incremental** khi đồ thị lớn và ít thay đổi; **deep matching** khi tên mơ hồ hoặc lần trước nhiều kết quả tin cậy thấp; **nâng ngưỡng** khi dữ liệu kém |
| Execute | Matching + Rule Engine + drift theo chiến lược; incremental thì tái sử dụng ánh xạ và finding của vùng không đổi |
| Knowledge | SQLite: ánh xạ, chữ ký package, cạnh thực tế, finding, drift, quyết định người dùng |

Luật P8–P10 điều khiển bước review: bật/tắt, phạm vi (mọi finding hay chỉ finding tin cậy thấp), ngân sách gọi LLM
(nhân đôi khi Coordinator ghi chú "nên bật kiểm tra lại bằng LLM").
Mọi quyết định được ghi trong `report.adaptation_trace` và `report.strategy.reasons`.

### 4 kịch bản

| Kịch bản | `scenario` | Gồm |
|---|---|---|
| 1. Khớp hoàn toàn | `match` | component ánh xạ chắc chắn + phụ thuộc convergence |
| 2. Code sai so với thiết kế | `wrong` | phụ thuộc bị cấm, ngược layer, ngược chiều, chu trình |
| 3. Code thiếu so với thiết kế | `missing` | component / phụ thuộc thiết kế không có trong code |
| 4. Code rác | `junk` | `orphan` (code chết), `undocumented` (ngoài tài liệu), `phantom` (import tới module không tồn tại) |
| (chưa đủ bằng chứng) | `uncertain` | ánh xạ chưa chắc, luật không quyết định |

`report.scenario_report` cho kết luận `match / mismatch / inconclusive`, kịch bản chính, số lượng, danh sách từng
kịch bản và đoạn tóm tắt.

## 4. Cấu trúc thư mục

```
consistency-checking-agent/
├── consistency_agent/
│   ├── schemas/          inputs.py (AEA, SCA, RuleSet, chỉ thị) · internal.py · outputs.py (ConsistencyReport)
│   ├── adapters/         architecture_adapter.py · code_adapter.py
│   ├── core/             naming · graph_matching · rule_engine · checker · confidence · drift
│   ├── adaptive/         mape_k.py · knowledge.py (SQLite: lịch sử, phản hồi, cache LLM)
│   ├── scenarios.py      phân loại 4 kịch bản + scenario_report
│   ├── rag/              retriever.py (BM25) · fewshot.py (19 ví dụ + ví dụ từ phản hồi) · corpus/*.md
│   ├── llm/              providers.py (groq / ollama / openai / openai_compat qua urllib; local-fewshot)
│   ├── review.py         RAG + few-shot + LLM review, cache, ngân sách, policy
│   ├── integration/      coordinator.py · recommendation.py · langgraph_node.py
│   ├── tools/            sca_fallback.py (envelope định dạng SCA bằng ast)
│   ├── agent.py          Facade + workflow
│   └── __main__.py       CLI
├── examples/
│   ├── py_clean_arch/    thiết kế + rule + envelope của bộ test SCA (bản gốc & bản cấy lỗi)
│   ├── scenarios/        4 kịch bản "Quản lý thư viện" (1_match, 2_wrong, 3_missing, 4_junk) + generate.py
│   ├── ecommerce/        thiết kế theo dữ liệu mẫu AEA (system.puml + description.md) + code mẫu
│   └── sample_outputs/   báo cáo JSON mẫu, CoordinatorInput, RecommendationRequest
├── tests/                64 test (6 kịch bản bắt buộc, 4 kịch bản đối chiếu, RAG/few-shot/LLM với server giả lập,
│                         adapter, rule, adaptive, tích hợp, CLI, tính tất định)
├── scripts/integration_check.py   chạy với code thật của Coordinator & Recommendation Agent
├── docs/                 ARCHITECTURE.md · METRICS.md · INTEGRATION.md · SCENARIOS_RAG_LLM.md
├── requirements.txt · pyproject.toml · .env.example
```

## 5. Dữ liệu demo

| Kịch bản | Nguồn | Kết quả mong đợi (đã kiểm chứng) |
|---|---|---|
| `scenarios/1_match` … `4_junk` | Viết riêng cho 4 kịch bản (mã Python thật) | lần lượt `match`, `wrong`, `missing`, `junk` (xem bảng trong docs/SCENARIOS_RAG_LLM.md) |
| `py_clean_arch` bản gốc | `tests/src/py-clean-arch` của repo SCA | 7/7 component ánh xạ đúng; drift 0.053 (low); 1 vi phạm thật: `usecases → di` (layer_violation) |
| `py_clean_arch` bản cấy lỗi | `tests/fault_src/f_py-clean-arch` của repo SCA | Bắt đủ 3 lỗi cấy: `controllers/rest/extension.py:25`, `controllers/graphql/pokemon/mapper.py:13` (→ `src.infrastructure`), `models/pokemon.py:8` (→ `src.domain`); drift tăng lên 0.217, trend `worsening` |
| `ecommerce` | `data/system.puml` + `description.md` của repo AEA | `web_app → order_service` vi phạm R1; `notification_service → order_service` ngược thiết kế; `analytics` ngoài kiến trúc |

Ghi chú: `design_architecture.json` của hai ví dụ được viết tay theo đúng schema AEA (AEA cần Groq API key
nên chưa chạy ở đây). Envelope code sinh bằng `extract-code` (dự phòng của SCA).

## 6. Tích hợp

* Đầu vào nhận **trực tiếp** output của AEA và SCA (đã đối chiếu schema trong repo của nhóm).
* Đầu ra `ConsistencyReport` cho Reporting Agent; `to_coordinator_input()` cho Coordinator;
  `to_recommendation_request()` cho Adaptive Recommendation Agent; `make_node()` cho LangGraph.
* Đã chạy thật với Coordinator và Recommendation Agent:

```bash
git clone https://github.com/tranganhne/coordinator-agent ../coordinator-agent
git clone https://github.com/huainguyn/Adaptive-Recommendation-Agent ../Adaptive-Recommendation-Agent
pip install -r ../coordinator-agent/requirements.txt   # thư viện của Coordinator (pyyaml, ...)
python scripts/integration_check.py --coordinator ../coordinator-agent --recommendation ../Adaptive-Recommendation-Agent
```

Chi tiết hợp đồng dữ liệu, ví dụ JSON vào/ra, đề xuất thay đổi cho agent khác: **[docs/INTEGRATION.md](docs/INTEGRATION.md)**.
Kiến trúc và kết quả khảo sát repo: **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

## 7. Hạn chế hiện tại

* Chưa chạy trực tiếp với output **thật** của AEA (cần Groq API key) và SCA (cần tree-sitter) trong
  môi trường phát triển; đã đối chiếu schema từ mã nguồn và dùng chính dữ liệu test của SCA.
* Phân tích phụ thuộc dựa trên **import tĩnh** (vì SCA hiện chỉ xuất import): không thấy lời gọi HTTP/RPC,
  import động, dependency injection lúc chạy. Vì vậy kết luận "thiếu phụ thuộc" có hệ số evidence 0.8.
* Mới hỗ trợ Python ở mức phân giải module (Java cần SCA xuất package Java; adapter đã tách riêng để mở rộng).
* Các trọng số/ngưỡng là giá trị khởi đầu, **chưa hiệu chỉnh** bằng bộ dữ liệu có đáp án (Precision/Recall).
* Từ điển đồng nghĩa trong `naming.py` còn nhỏ, chủ yếu tiếng Anh.
* Đường LLM mới được kiểm thử với server giả lập chuẩn OpenAI, **chưa chạy với LLM thật** (môi trường phát triển không
  có API key). Bộ `local-fewshot` mặc định là kNN trên ví dụ, không phải mô hình ngôn ngữ: lời giải thích của nó được
  mượn từ ví dụ gần nhất nên đôi khi chưa sát.
* RAG dùng BM25 (từ khóa), chưa dùng embedding ngữ nghĩa.

## 8. Hướng phát triển

1. Bộ benchmark có đáp án (như `benchmark/` của Coordinator) để đo Precision/Recall/F1 và hiệu chỉnh trọng số.
2. Đánh giá LLM thật (Groq/Ollama) trên bộ có đáp án: tỷ lệ đồng ý với người, trước khi bật `resolve`.
3. Thêm embedding (vd sentence-transformers) cho RAG khi corpus lớn.
4. Nhận rule do AEA trích xuất từ ADR/README; nhận `properties.layer` từ AEA.
5. Hỗ trợ Java (package/import) khi SCA mở rộng; dùng thêm quan hệ `call`/`reference`.
6. Giao diện xác nhận (Streamlit) cho human-in-the-loop.
