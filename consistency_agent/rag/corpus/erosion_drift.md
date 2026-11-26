# Architecture erosion và architecture drift

## Định nghĩa
Architecture drift là sự khác biệt dần dần giữa kiến trúc mô tả và kiến trúc hiện thực mà chưa vi phạm nguyên tắc cốt lõi, ví dụ thêm package không được ghi trong tài liệu. Architecture erosion là vi phạm nguyên tắc kiến trúc, ví dụ phá quy tắc phụ thuộc hoặc layer.

## Dấu hiệu erosion
Chu trình phụ thuộc giữa các component, phụ thuộc ngược chiều thiết kế, gọi tắt qua layer, package dùng chung phình to, code chết không còn được gọi, import tới module không còn tồn tại sau khi refactor.

## Chu trình phụ thuộc
Chu trình làm các component không thể tách, triển khai hay kiểm thử độc lập. Cách phá: tách interface dùng chung ra module riêng, đảo phụ thuộc bằng abstraction, hoặc chuyển sang giao tiếp bằng event hay callback.
