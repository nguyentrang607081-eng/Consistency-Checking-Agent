# Bốn kịch bản đối chiếu kiến trúc và mã nguồn

## Kịch bản 1 - khớp hoàn toàn
Mọi component trong kiến trúc thiết kế đều tìm thấy package tương ứng trong mã nguồn với ánh xạ chắc chắn, mọi phụ thuộc trong code đều có trong thiết kế (convergence) và không có package nào ngoài kiến trúc. Khi khớp hoàn toàn, drift bằng 0 và không có vi phạm đang mở.

## Kịch bản 2 - code sai so với thiết kế
Mã nguồn có phụ thuộc trái với thiết kế: gọi tắt qua layer (API gọi thẳng repository), phụ thuộc ngược chiều (domain import service), vi phạm luật cấm, tạo chu trình phụ thuộc, hoặc phụ thuộc giữa hai component thiết kế mà thiết kế không cho phép. Đây là dạng divergence trong Reflexion Model và là dấu hiệu architecture erosion.

## Kịch bản 3 - code thiếu so với thiết kế
Thiết kế mô tả component hoặc phụ thuộc nhưng mã nguồn không có (absence). Nguyên nhân: chưa hiện thực, đã bị xóa nhưng tài liệu chưa cập nhật, component nằm ở package khác tên, hoặc phụ thuộc được thực hiện lúc chạy (HTTP, message queue, dependency injection) nên phân tích import tĩnh không thấy.

## Kịch bản 4 - code rác, thiết kế không có
Mã nguồn chứa package không được mô tả trong kiến trúc. Có ba dạng: orphan - không component thiết kế nào dùng tới, ứng viên code chết cần xóa; undocumented - được component thiết kế sử dụng nên cần bổ sung vào tài liệu kiến trúc; phantom - import tới package không tồn tại, sẽ gây ImportError khi chạy.
