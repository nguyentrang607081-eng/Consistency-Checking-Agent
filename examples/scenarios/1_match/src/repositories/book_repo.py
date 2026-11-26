"""Repositories: truy cập CSDL sách."""
import sqlite3

from models.book import Book


class BookRepository:
    def get(self, book_id: int) -> Book:
        _ = sqlite3
        return Book(id=book_id, title="Clean Architecture")
