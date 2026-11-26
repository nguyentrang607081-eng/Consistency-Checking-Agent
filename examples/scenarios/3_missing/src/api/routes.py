"""API: nhận request mượn/trả sách."""
from services.loan_service import LoanService


def borrow(book_id: int, reader_id: int) -> dict:
    return LoanService().borrow(book_id, reader_id)
