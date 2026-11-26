# Mẫu khắc phục vi phạm

## Sửa phụ thuộc bị cấm
Xóa import trực tiếp, đi qua component trung gian được phép (service, use case, facade). Nếu phụ thuộc là có chủ đích, cập nhật tài liệu kiến trúc và thêm luật allow để tránh báo lại.

## Sửa phụ thuộc ngược chiều
Đặt interface ở component được phép bị phụ thuộc, dùng callback, observer hoặc event để đảo chiều. Không để domain import service hay controller.

## Xử lý component thiếu
Kiểm tra component có nằm ở package khác tên không và khai báo ánh xạ; nếu chưa hiện thực thì đưa vào backlog; nếu đã bỏ thì cập nhật tài liệu kiến trúc.

## Xử lý code rác
Code orphan: xác nhận không còn được gọi (kể cả qua entry point, script, test) rồi xóa. Package undocumented: bổ sung vào tài liệu kiến trúc hoặc gộp vào component phù hợp. Import phantom: sửa đường dẫn import hoặc xóa import, vì chương trình sẽ lỗi khi chạy tới dòng đó.
