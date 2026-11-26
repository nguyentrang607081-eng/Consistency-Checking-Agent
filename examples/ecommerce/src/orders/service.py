"""Order Service: nghiệp vụ đơn hàng."""
from notify.sender import send_confirmation
from .repository import OrderRepository


class OrderService:
    def find(self, order_id: int) -> dict:
        order = OrderRepository().get(order_id)
        send_confirmation(order)
        return order
