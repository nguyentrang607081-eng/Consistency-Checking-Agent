"""API Gateway: điều phối request tới các service."""
from orders.service import OrderService


class ApiClient:
    def get_order(self, order_id: int) -> dict:
        return OrderService().find(order_id)
