"""Sinh bộ dữ liệu demo 4 kịch bản cho hệ thống "Quản lý thư viện".

Cùng một kiến trúc thiết kế (design_architecture.json) + bộ luật (rules.json), 4 phiên bản mã nguồn:

  1_match   : code khớp hoàn toàn thiết kế
  2_wrong   : code SAI so với thiết kế   (API gọi thẳng Repository; Models phụ thuộc ngược lên Services)
  3_missing : code THIẾU so với thiết kế (không có Notifications; Services không dùng Models)
  4_junk    : code RÁC                    (legacy/ không ai dùng; utils/ ngoài kiến trúc; import package không tồn tại)

Chạy:  python examples/scenarios/generate.py
-> ghi <kịch bản>/src/... (mã Python thật) và <kịch bản>/code_envelope.json (định dạng SCA).
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent))

from consistency_agent.tools.sca_fallback import build_envelope  # noqa: E402

BASE: dict[str, str] = {
    "api/routes.py": '''"""API: nhận request mượn/trả sách."""
from services.loan_service import LoanService


def borrow(book_id: int, reader_id: int) -> dict:
    return LoanService().borrow(book_id, reader_id)
''',
    "services/loan_service.py": '''"""Services: nghiệp vụ mượn sách."""
from models.book import Book
from notifications.mailer import send_mail
from repositories.book_repo import BookRepository


class LoanService:
    def borrow(self, book_id: int, reader_id: int) -> dict:
        book: Book = BookRepository().get(book_id)
        send_mail(reader_id, f"Bạn đã mượn {book.title}")
        return {"book": book.title, "reader": reader_id}
''',
    "repositories/book_repo.py": '''"""Repositories: truy cập CSDL sách."""
import sqlite3

from models.book import Book


class BookRepository:
    def get(self, book_id: int) -> Book:
        _ = sqlite3
        return Book(id=book_id, title="Clean Architecture")
''',
    "models/book.py": '''"""Models: thực thể sách."""
from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str
''',
    "notifications/mailer.py": '''"""Notifications: gửi email."""
import smtplib


def send_mail(reader_id: int, text: str) -> None:
    _ = (smtplib, reader_id, text)
''',
}

VARIANTS: dict[str, dict[str, str | None]] = {
    "1_match": {},
    "2_wrong": {
        "api/routes.py": '''"""API: nhận request mượn/trả sách."""
from repositories.book_repo import BookRepository  # SAI: API truy cập thẳng Repository
from services.loan_service import LoanService


def borrow(book_id: int, reader_id: int) -> dict:
    if BookRepository().get(book_id) is None:
        return {}
    return LoanService().borrow(book_id, reader_id)
''',
        "models/book.py": '''"""Models: thực thể sách."""
from dataclasses import dataclass


@dataclass
class Book:
    id: int
    title: str

    def is_available(self) -> bool:
        from services.loan_service import LoanService  # SAI: Domain phụ thuộc ngược lên Services
        return LoanService is not None
''',
    },
    "3_missing": {
        "notifications/mailer.py": None,  # THIẾU: không hiện thực Notifications
        "services/loan_service.py": '''"""Services: nghiệp vụ mượn sách (chưa gửi thông báo, chưa dùng Models)."""
from repositories.book_repo import BookRepository


class LoanService:
    def borrow(self, book_id: int, reader_id: int) -> dict:
        book = BookRepository().get(book_id)
        return {"book": getattr(book, "title", ""), "reader": reader_id}
''',
    },
    "4_junk": {
        "legacy/old_report.py": '''"""Báo cáo cũ - không còn được gọi ở đâu (code chết)."""
from models.book import Book


def export_all() -> list[Book]:
    return []
''',
        "utils/text.py": '''"""Tiện ích chuỗi - có dùng nhưng tài liệu kiến trúc không mô tả."""


def normalize(text: str) -> str:
    return text.strip().lower()
''',
        "services/loan_service.py": '''"""Services: nghiệp vụ mượn sách."""
from models.book import Book
from notifications.mailer import send_mail
from repositories.book_repo import BookRepository
from utils.text import normalize


class LoanService:
    def borrow(self, book_id: int, reader_id: int) -> dict:
        book: Book = BookRepository().get(book_id)
        send_mail(reader_id, normalize(f"Bạn đã mượn {book.title}"))
        return {"book": book.title, "reader": reader_id}
''',
        "api/routes.py": '''"""API: nhận request mượn/trả sách."""
from services.loan_service import LoanService


def borrow(book_id: int, reader_id: int) -> dict:
    from src.cache.redis_client import cache  # RÁC: import tới package không tồn tại
    return cache or LoanService().borrow(book_id, reader_id)
''',
    },
}


def main() -> None:
    for name, changes in VARIANTS.items():
        files = dict(BASE)
        for path, content in changes.items():
            if content is None:
                files.pop(path, None)
            else:
                files[path] = content
        src = HERE / name / "src"
        if src.exists():
            shutil.rmtree(src)
        for path, content in files.items():
            target = src / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            init = target.parent / "__init__.py"
            if not init.exists():
                init.write_text("", encoding="utf-8")
        env = build_envelope(src, project_id=f"library-{name}", run_id=f"scan-{name}")
        env["warnings"] = []
        (HERE / name / "code_envelope.json").write_text(json.dumps(env, ensure_ascii=False, indent=2) + "\n",
                                                        encoding="utf-8")
        print(f"{name}: {len(files)} file, {len(env['payload']['dependencies'])} import")


if __name__ == "__main__":
    main()
