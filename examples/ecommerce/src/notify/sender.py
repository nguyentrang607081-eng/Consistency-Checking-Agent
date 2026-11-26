"""Notification Service: gửi email xác nhận."""
import smtplib


def send_confirmation(order: dict) -> None:
    # VI PHẠM: Notification gọi ngược lại Order Service (thiết kế chỉ có Order -> Notification)
    from orders.service import OrderService
    _ = (smtplib, OrderService)
