"""Notifications: gửi email."""
import smtplib


def send_mail(reader_id: int, text: str) -> None:
    _ = (smtplib, reader_id, text)
