"""Giao diện web (Web App)."""
from gateway.client import ApiClient
from orders.repository import OrderRepository  # VI PHẠM: UI truy cập thẳng Order Service, bỏ qua API Gateway


def order_page(order_id: int) -> dict:
    cached = OrderRepository().get(order_id)
    return cached or ApiClient().get_order(order_id)
