# Giới hạn của phân tích phụ thuộc tĩnh

## Phụ thuộc lúc chạy
Phân tích import tĩnh không thấy lời gọi HTTP, gRPC, message queue (Kafka, RabbitMQ), import động (importlib), plugin hay dependency injection lúc chạy. Quan hệ thiết kế loại sync_call hoặc async_call giữa các service triển khai riêng thường không xuất hiện dưới dạng import, nên kết luận thiếu phụ thuộc cần được xác nhận.

## Import trong hàm và import có điều kiện
Import đặt trong hàm vẫn là phụ thuộc thật nhưng thường là dấu hiệu né chu trình import. Import trong khối TYPE_CHECKING chỉ phục vụ kiểm tra kiểu, không tạo phụ thuộc lúc chạy.

## Mã kiểm thử và script
Package tests, scripts, migrations thường không thuộc kiến trúc và nên được loại khỏi đối chiếu; báo chúng là code rác là báo nhầm.
