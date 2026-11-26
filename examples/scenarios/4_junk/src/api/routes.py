"""API: nhận request mượn/trả sách."""
from services.loan_service import LoanService


def borrow(book_id: int, reader_id: int) -> dict:
    from src.cache.redis_client import cache  # RÁC: import tới package không tồn tại
    return cache or LoanService().borrow(book_id, reader_id)
