# Kiến trúc phân lớp

## Phân lớp chặt và lỏng
Kiến trúc phân lớp xếp component thành các layer từ ngoài vào trong (presentation, application, domain, infrastructure). Phân lớp lỏng (relaxed) cho phép layer ngoài gọi bất kỳ layer bên trong; phân lớp chặt (strict) chỉ cho phép gọi layer kề dưới. Gọi ngược lên layer ngoài luôn là vi phạm.

## Gọi tắt qua layer
Presentation gọi thẳng repository hoặc database là gọi tắt (layer skipping): logic nghiệp vụ bị phân tán, khó kiểm soát giao dịch và phân quyền. Cách sửa: chuyển lời gọi vào service hoặc use case và để presentation chỉ gọi service.
