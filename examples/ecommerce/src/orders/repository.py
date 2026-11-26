"""Truy cập CSDL đơn hàng."""
import sqlite3


class OrderRepository:
    def get(self, order_id: int) -> dict:
        with sqlite3.connect(":memory:") as conn:
            conn.execute("select 1")
        return {"id": order_id}
