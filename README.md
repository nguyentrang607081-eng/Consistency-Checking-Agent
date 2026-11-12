# Consistency Checking Agent (CCA)

Agent số 4 trong hệ thống Adaptive Multi-Agent, thực hiện pha **Analyze** của vòng lặp MAPE-K. CCA nhận ArchitectureGraph (thiết kế) và CodeGraph (mã nguồn), ánh xạ nút, nâng cạnh, áp luật kiến trúc và trả ConsistencyReport JSON.

## Thành phần
- Pydantic contracts: xác thực ArchitectureGraph schema 1.0.0 và CodeGraph adapter.
- Element mapper: exact → path rule → RapidFuzz; trường hợp mơ hồ không tự gán.
- Checkers: layer skipping/back call, cross-module, SCC cycle, architecture drift.
- Aggregator: loại trùng, severity/confidence, mapping coverage và consistency score.
- LangGraph: load/map → checkers song song → aggregate.
- FastAPI: POST /check-consistency, GET /health.

## Cài đặt
Python 3.11+:
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env
```
Mặc định ENABLE_LLM=false; không cần API key để chạy kiểm thử. Chạy API:
```bash
uvicorn consistency_checking_agent.interface:app --reload
```
Mở http://127.0.0.1:8000/docs để thử API.

## Payload tối thiểu
```json
{
  "system_name": "Sample",
  "architecture_graph": {
    "schema_version": "1.0.0",
    "nodes": [
      {"id":"presentation.OrderController","name":"OrderController","type":"service","properties":{"layer":"presentation"}},
      {"id":"business.OrderService","name":"OrderService","type":"service","properties":{"layer":"business"}},
      {"id":"data_access.OrderRepository","name":"OrderRepository","type":"database","properties":{"layer":"data_access"}}
    ],
    "edges": [{"id":"a1","source_id":"presentation.OrderController","target_id":"business.OrderService","relationship_type":"sync_call"}]
  },
  "code_graph": {
    "nodes": [
      {"id":"src/presentation/order_controller.py","name":"OrderController","type":"module","file_path":"src/presentation/order_controller.py"},
      {"id":"src/data_access/order_repository.py","name":"OrderRepository","type":"module","file_path":"src/data_access/order_repository.py"}
    ],
    "edges": [{"source_id":"src/presentation/order_controller.py","target_id":"src/data_access/order_repository.py","relationship_type":"import","file_path":"src/presentation/order_controller.py","line_number":42,"code_snippet":"from data_access import OrderRepository"}]
  }
}
```

## Kiểm thử
```bash
pytest -q
```

## Giả định và giới hạn hiện tại
- CodeGraph là adapter theo ví dụ trong đặc tả; schema chính thức cần đồng bộ với nhóm Source Code Analysis.
- Layer được lấy từ rules.node_layers, properties.layer, hoặc tiền tố ID khớp rules.layers.
- Module chỉ được xác định khi khai báo qua rules.node_modules hoặc rules.modules.
- ENABLE_LLM mặc định tắt. Lớp drift classifier/cache là adapter tùy chọn; cần cung cấp client có phương thức async classify_drift(candidate) trước khi bật.
- Hỗ trợ dữ liệu CodeGraph hiện tại; không tự phân tích source code thô. Python là phạm vi giai đoạn đầu theo đặc tả.
- consistency_score tính trên cạnh đã nâng; khi không có cạnh nâng được, trả 1.0 và warnings giải thích trường hợp mapping.
- Không diễn giải missing_edge thành violation; đây chỉ là drift_candidate.
