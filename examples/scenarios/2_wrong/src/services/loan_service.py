"""Services: nghiệp vụ mượn sách."""
from models.book import Book
from notifications.mailer import send_mail
from repositories.book_repo import BookRepository


class LoanService:
    def borrow(self, book_id: int, reader_id: int) -> dict:
        book: Book = BookRepository().get(book_id)
        send_mail(reader_id, f"Bạn đã mượn {book.title}")
        return {"book": book.title, "reader": reader_id}
