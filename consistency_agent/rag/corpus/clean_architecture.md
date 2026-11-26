# Clean Architecture và quy tắc phụ thuộc

## Quy tắc phụ thuộc (Dependency Rule)
Trong Clean Architecture, phụ thuộc mã nguồn chỉ được hướng vào trong: framework và driver phụ thuộc interface adapter (controller, presenter, repository), interface adapter phụ thuộc use case, use case phụ thuộc entity. Entity và use case không được biết tới web framework, ORM hay cơ sở dữ liệu. Vi phạm hướng này là vi phạm layer.

## Đảo ngược phụ thuộc (Dependency Inversion)
Khi layer trong cần gọi chức năng của layer ngoài, định nghĩa interface (abstract class, Protocol) ở layer trong và để layer ngoài hiện thực interface đó. Ví dụ use case khai báo AbstractRepository, package repositories hiện thực SqlRepository; use case chỉ import abstraction.

## Độc lập framework và cơ sở dữ liệu
Domain entity không được import sqlalchemy, fastapi, django. Nếu cần ánh xạ ORM, đặt mapper trong layer adapter. Use case không phụ thuộc framework web để có thể kiểm thử không cần server.
