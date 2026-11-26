# 4 kịch bản đối chiếu + RAG + Few-shot + LLM

## 1. Bốn kịch bản

| # | Kịch bản (`scenario`) | Ý nghĩa | Finding thuộc kịch bản |
|---|---|---|---|
| 1 | `match` | Cặp dữ liệu **khớp hoàn toàn** | (không có finding) – component ánh xạ chắc chắn + phụ thuộc convergence |
| 2 | `wrong` | **Code sai** so với thiết kế | `forbidden_dependency`, `layer_violation`, `reverse_dependency`, `cyclic_dependency`, `unexpected_dependency` giữa hai component thiết kế |
| 3 | `missing` | **Code thiếu** so với thiết kế | `missing_component`, `missing_dependency` |
| 4 | `junk` | **Code rác** – thiết kế không có | `unexpected_component` (chi tiết: `orphan` = không ai dùng → code chết, `undocumented` = được dùng nhưng tài liệu thiếu, `phantom` = import tới package không tồn tại) và phụ thuộc nối tới các package đó |
| + | `uncertain` | Chưa đủ bằng chứng | `uncertain_mapping`, finding có status `undetermined` |

Việc xếp loại là **tất định** (`consistency_agent/scenarios.py`). Báo cáo có thêm `scenario_report`:

```json
"scenario_report": {
  "verdict": "mismatch",                 // match | mismatch | inconclusive
  "primary_scenario": "junk",            // kịch bản nặng nhất (tổng trọng số mức độ)
  "counts": {"match": 10, "wrong": 0, "missing": 0, "junk": 6, "uncertain": 0},
  "match":   [{"kind": "dependency", "source": "api", "target": "services", "detail": "convergence"}],
  "junk":    [{"kind": "component", "source": "legacy", "detail": "orphan", "finding_ids": ["CCA-0002"]}],
  "match_ratio": 0.625,
  "summary": "Kết luận: KHÔNG khớp; kịch bản chính: code rác ...",
  "summary_by": "template"              // hoặc tên model LLM nếu LLM viết tóm tắt
}
```

Mỗi finding có thêm `scenario`, `scenario_detail`, `llm_review`.

### Bộ dữ liệu demo 4 kịch bản

`examples/scenarios/` – hệ thống *Quản lý thư viện*: cùng một thiết kế (`design_architecture.json`) và bộ luật
(`rules.json`), 4 phiên bản mã nguồn Python thật (sinh bằng `examples/scenarios/generate.py`):

| Thư mục | Nội dung code | Kết quả (đã chạy) |
|---|---|---|
| `1_match` | Đúng thiết kế | `match`, drift 0.000, 0 finding |
| `2_wrong` | `api` import thẳng `repositories` (vi phạm R1); `models` import ngược `services` | `mismatch / wrong`, drift 0.167 |
| `3_missing` | Không có `notifications`; `services` không dùng `models` | `mismatch / missing`, drift 0.300 |
| `4_junk` | `legacy/` không ai dùng; `utils/` ngoài kiến trúc; import `src.cache...` không tồn tại | `mismatch / junk`, drift 0.375 |

```bash
python -m consistency_agent scenarios          # chạy cả 4, in bảng tổng hợp, ghi outputs/scenario_*.report.json
python -m consistency_agent scenarios -v       # in thêm nhận xét của reviewer cho từng finding
```

## 2. Kiến trúc RAG + few-shot + LLM

```
finding ──► truy vấn ──► RAG (BM25) ───────────────► top-4 tài liệu
   │                     ├ corpus kiến trúc (21 mục: 4 kịch bản, Clean Arch, layer, Reflexion, erosion, khắc phục, giới hạn phân tích tĩnh)
   │                     ├ design:<id>   bằng chứng văn bản từ Architecture Extraction Agent
   │                     ├ rule:<id>     luật / layer của dự án
   │                     └ case:<fp>     phản hồi người dùng đã lưu (case memory)
   ├──────► Few-shot selector ──────────────────────► top-3 ví dụ có lời giải
   │                     ├ 19 ví dụ viết tay (gồm 4 ví dụ "báo nhầm" để dạy khi nào KHÔNG kết luận)
   │                     └ fb-<fp>  ví dụ sinh từ phản hồi người dùng (accepted / false_positive)
   └──────► Reviewer
            ├ LLM (groq / ollama / openai / openai_compat): system prompt + few-shot dạng hội thoại user/assistant
            │       + ngữ cảnh RAG + finding → JSON {scenario, verdict, confidence, rationale, suggested_fix, cited_docs}
            └ local-fewshot (mặc định, KHÔNG phải LLM): bỏ phiếu có trọng số trên ví dụ few-shot + giải thích từ RAG
```

### Các nguyên tắc an toàn

* **LLM là ý kiến thứ hai**: kết quả nằm trong `finding.llm_review`, tách khỏi `confidence_score` (công thức giải thích được).
* **Policy** (`CCA_LLM_POLICY`):
  * `advisory` (mặc định): chỉ ghi ý kiến.
  * `resolve`: LLM thật được nâng `needs_review → confirmed` khi `verdict = confirm` và `confidence ≥ 0.75`.
  * LLM **không bao giờ tự gỡ** một finding: `reject` chỉ là khuyến nghị, người dùng quyết định (`feedback false-positive`).
  * Bộ `local-fewshot` không được đổi trạng thái (nó không mang bằng chứng mới).
* **Chống bịa**: `cited_docs` chỉ giữ id có trong ngữ cảnh RAG; JSON sai định dạng → bỏ, dùng bộ cục bộ (`fallbacks`).
* `review.disagreements` đếm finding mà reviewer bác bỏ hoặc xếp kịch bản khác → ưu tiên người xem lại.

### Chi phí và thích ứng (MAPE-K)

| Luật | Nội dung |
|---|---|
| P8 | Chọn chế độ review theo reviewer cấu hình: `off` / `local` / `llm`; Coordinator có thể tắt (`llm_review: false`) |
| P9 | Có LLM: đồ thị nhỏ → rà soát mọi finding; đồ thị lớn → chỉ finding tin cậy thấp |
| P10 | Lần trước > 30% kết quả tin cậy thấp **hoặc Coordinator ghi chú "nên bật kiểm tra lại bằng LLM"** → nhân đôi ngân sách (tối đa 100) |

* Ngân sách `CCA_LLM_MAX_CALLS` (mặc định 20 lượt / lần quét); hết ngân sách → các finding còn lại dùng bộ cục bộ.
* Cache câu trả lời theo `sha256(model + prompt)` trong SQLite (`llm_cache`) → quét lại không tốn lượt gọi.
* `performance.llm_calls` ghi số lần gọi API thật để so sánh chi phí.

### Học từ phản hồi

```bash
python -m consistency_agent feedback false-positive --project library-4_junk --fingerprint <fp> --note "legacy là tool chạy tay"
```
Lần quét sau: phản hồi thành tài liệu `case:<fp>` trong RAG **và** ví dụ few-shot `fb-<fp>`, được ưu tiên cho các finding
liên quan cùng component (test `test_feedback_becomes_case_memory_and_few_shot`).

## 3. Cấu hình LLM

| Provider | Lệnh | Ghi chú |
|---|---|---|
| `local` | (mặc định) | Offline, không cần key. Không phải LLM. |
| `ollama` | `ollama pull qwen2.5:7b-instruct` rồi `--llm-provider ollama` | Miễn phí, chạy trên máy (Windows có bản cài Ollama). |
| `groq` | đặt `GROQ_API_KEY`, `--llm-provider groq` | Cùng key/model với Architecture Extraction Agent (`llama-3.3-70b-versatile`); có gói miễn phí. |
| `openai` | đặt `OPENAI_API_KEY`, `--llm-provider openai` | Trả phí. |
| `openai_compat` | `--llm-provider openai_compat --llm-base-url http://host:port/v1 --llm-model ...` | LM Studio, vLLM, OpenRouter... |

Ví dụ (PowerShell):
```powershell
$env:GROQ_API_KEY = "gsk_..."
python -m consistency_agent scenarios --llm-provider groq --llm-policy advisory -v
```

Thiếu key → agent vẫn chạy bằng bộ cục bộ và ghi cảnh báo trong `report.warnings`.

## 4. Trạng thái kiểm thử

* Đường LLM được kiểm thử bằng **server giả lập chuẩn OpenAI** chạy cục bộ (`tests/test_review.py`): kiểm tra prompt có
  3 cặp few-shot + ngữ cảnh RAG, header Authorization, temperature 0, parse JSON, lọc `cited_docs` bịa, cache,
  ngân sách, fallback khi trả lời rác, policy resolve, gợi ý LLM từ Coordinator.
* **Chưa chạy với một LLM thật** trong môi trường phát triển (không có API key). Chất lượng nhận xét của LLM thật cần
  được đánh giá trên bộ dữ liệu có đáp án trước khi dùng `resolve`.
