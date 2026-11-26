"""API: nhận request mượn/trả sách."""
from repositories.book_repo import BookRepository  # SAI: API truy cập thẳng Repository
from services.loan_service import LoanService


def borrow(book_id: int, reader_id: int) -> dict:
    if BookRepository().get(book_id) is None:
        return {}
    return LoanService().borrow(book_id, reader_id)
