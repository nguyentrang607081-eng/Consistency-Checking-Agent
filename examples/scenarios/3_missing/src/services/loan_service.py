"""Services: nghiệp vụ mượn sách (chưa gửi thông báo, chưa dùng Models)."""
from repositories.book_repo import BookRepository


class LoanService:
    def borrow(self, book_id: int, reader_id: int) -> dict:
        book = BookRepository().get(book_id)
        return {"book": getattr(book, "title", ""), "reader": reader_id}
