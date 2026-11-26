"""Models: thực thể sách."""
from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str

    def is_available(self) -> bool:
        from services.loan_service import LoanService  # SAI: Domain phụ thuộc ngược lên Services
        return LoanService is not None
