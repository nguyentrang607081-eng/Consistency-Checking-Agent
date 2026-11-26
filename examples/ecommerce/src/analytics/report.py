"""Thống kê doanh thu - KHÔNG có trong tài liệu kiến trúc."""
from orders.repository import OrderRepository


def revenue() -> int:
    return len(OrderRepository().get(1))
