# Công thức tính điểm

Mọi chỉ số đều tính lại được bằng tay từ dữ liệu trong `ConsistencyReport`. Các hằng số (trọng số,
ngưỡng) là **giá trị khởi đầu**, cần hiệu chỉnh bằng thực nghiệm (Precision/Recall trên bộ dữ liệu có đáp án).

## 1. Điểm ánh xạ component (Graph Matching)

| Phương pháp tên | Điểm | Ví dụ |
|---|---|---|
| exact | 1.00 | "Use Cases" ↔ `usecases` |
| core (bỏ hậu tố service/manager/api…) | 0.88 | "Order Service" ↔ `orders` |
| head (một token trùng tên package) | 0.80 | "REST Controllers" ↔ `controllers` |
| acronym | 0.75 | "Dependency Injection" ↔ `di` |
| synonym (từ điển kiến trúc) | 0.60 | "Domain Entities" ↔ `models` |
| fuzzy (difflib / Jaccard) | ≤ 0.70 | "Payment Processor" ↔ `paymnt_proc` |

```
name'  = name × (1 − 0.03 × (độ_sâu_package − 1))
struct = Σ trọng_số(láng giềng khớp chiều) / số láng giềng đã neo      (None nếu chưa có láng giềng neo)
conf   = 1 − (1 − 0.85·name') × (1 − 0.70·struct)
```

* `conf ≥ 0.75` và cách ứng viên thứ hai ≥ 0.08 → `confirmed`
* `0.40 ≤ conf` → `uncertain` (cần xác nhận)
* còn lại → `unmapped`

## 2. Confidence score của finding

```
confidence = mapping × rule_clarity × evidence × data_completeness
```

| Yếu tố | Giá trị |
|---|---|
| mapping | min(conf ánh xạ hai đầu); node chỉ có trong code: 0.9; missing component: max(0.3, 1 − điểm ứng viên gần nhất) |
| rule_clarity | luật tường minh 1.0 · layer 0.95 · phát hiện chu trình 0.9 · component thiếu 0.9 · package ma 0.9 · cạnh thiết kế 0.85 · component thừa 0.8 · ngược chiều thiết kế 0.7 · `design_only` 0.65 · `unspecified`/luật mâu thuẫn 0.5 |
| evidence | có câu lệnh import (file+dòng) 1.0 · import tới package không tồn tại 0.85 · kết luận dựa trên **vắng mặt** import 0.8 |
| data_completeness | SCA `SUCCESS` 1.0 / `PARTIAL` 0.8 / `FAILED` 0.5 × độ tin cậy TB của node thiết kế liên quan (do AEA gán) |

Trạng thái: `confidence ≥ threshold` (mặc định 0.7, đồng bộ Coordinator) → `confirmed`; ngược lại
`needs_review`; luật không quyết định → `undetermined`; người dùng đã chấp nhận → `accepted`.

Ví dụ (e-commerce): `notification_service -> order_service` ngược chiều thiết kế:
mapping = min(0.80, 0.90) = 0.80; rule_clarity = 0.7; evidence = 1.0;
data_completeness = 1.0 × (0.85 + 1.0)/2 = 0.925 → **0.52 → needs_review** (vì node Notification do AEA
trích từ văn bản với độ tin cậy 0.85 và ánh xạ `notify` chỉ dựa trên cấu trúc + tên mờ).

## 3. Mức độ nghiêm trọng (severity)

| Loại | Mặc định |
|---|---|
| forbidden_dependency | theo luật, mặc định high |
| layer_violation | high (strict bỏ layer: medium) |
| cyclic_dependency | high (2 node) / medium |
| reverse_dependency | medium |
| unexpected_dependency | medium (`design_only`) / low (`unspecified`) |
| missing_dependency | medium (từ thiết kế) / theo luật `require` (mặc định high) |
| missing_component | high nếu component có quan hệ trong thiết kế, ngược lại medium |
| unexpected_component | high nếu là package ma; medium nếu nối với component thiết kế; còn lại low |
| uncertain_mapping | low |

## 4. Similarity và Drift

Sau ánh xạ, hai đồ thị dùng chung không gian node:

```
structural_similarity = (|N_D ∩ N_A| + |E_D ∩ E_A|) / (|N_D ∪ N_A| + |E_D ∪ E_A|)
drift_score           = 1 − structural_similarity
```

`drift_score` chính là **khoảng cách sửa đồ thị (graph edit distance) chuẩn hóa** khi ánh xạ node đã
biết và chỉ dùng phép thêm/xóa node, cạnh: số thao tác cần = |N_D Δ N_A| + |E_D Δ E_A|.

```
dependency_conformance = |E_D ∩ E_A| / |E_A|   (bao nhiêu % phụ thuộc thực tế đúng thiết kế)
dependency_realization = |E_D ∩ E_A| / |E_D|   (bao nhiêu % phụ thuộc thiết kế đã hiện thực)
violation_index        = Σ w(severity) × confidence (vi phạm đang mở) / (|N_D ∪ N_A| + |E_D ∪ E_A|)
                         w: low .25, medium .5, high .75, critical 1.0
```

`drift_level`: < 0.05 none · < 0.20 low · < 0.40 moderate · < 0.60 high · còn lại severe.
`drift_trend` (so với lần quét trước trong Knowledge): chênh > +0.02 worsening, < −0.02 improving.

Ví dụ kiểm chứng (py-clean-arch bản gốc): N_D = N_A = 7 component, E_D = 11, E_A = 12, giao = 11
→ similarity = (7 + 11)/(7 + 12) = 0.947, drift = 0.053 (low). Cạnh lệch duy nhất: `usecases -> di`.
