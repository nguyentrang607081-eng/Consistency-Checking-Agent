"""KNOWLEDGE (chữ K trong MAPE-K): kho tri thức cục bộ của CCA, lưu bằng SQLite.

CCA chỉ ghi tầng tri thức RIÊNG của mình (ánh xạ, lịch sử quét, quyết định của người dùng).
Tầng "unified findings" của Kho tri thức chung vẫn do Coordinator ghi (đúng sơ đồ nhóm).

Bảng:
  scans      : mỗi lần kiểm tra (drift, chiến lược, chữ ký package, cạnh thực tế, thống kê)
  mappings   : ánh xạ component -> package (nguồn 'auto' hoặc 'user'), có trạng thái rejected
  findings   : finding của từng lần quét (để carry-over khi quét incremental)
  decisions  : quyết định của người dùng trên finding (accepted / false_positive)
  llm_cache  : cache câu trả lời LLM theo hash(prompt) -> không gọi lại API cho cùng một câu hỏi
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    project_id TEXT NOT NULL, scan_id TEXT NOT NULL, created_at TEXT NOT NULL, commit_hash TEXT,
    drift_score REAL, design_signature TEXT, package_signatures TEXT, actual_edges TEXT,
    strategy TEXT, stats TEXT, PRIMARY KEY (project_id, scan_id));
CREATE TABLE IF NOT EXISTS mappings (
    project_id TEXT NOT NULL, design_id TEXT NOT NULL, packages TEXT NOT NULL, confidence REAL NOT NULL,
    source TEXT NOT NULL, status TEXT NOT NULL, updated_at TEXT NOT NULL,
    PRIMARY KEY (project_id, design_id, source, packages));
CREATE TABLE IF NOT EXISTS findings (
    project_id TEXT NOT NULL, scan_id TEXT NOT NULL, fingerprint TEXT NOT NULL, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key TEXT PRIMARY KEY, response TEXT NOT NULL, model TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
    project_id TEXT NOT NULL, fingerprint TEXT NOT NULL, decision TEXT NOT NULL, note TEXT,
    created_at TEXT NOT NULL, PRIMARY KEY (project_id, fingerprint));
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class KnowledgeStore:
    """Bao gói SQLite. ``path=":memory:"`` để chạy test không ghi file."""

    def __init__(self, path: str | Path = "data/cca_knowledge.sqlite") -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._mem: Optional[sqlite3.Connection] = sqlite3.connect(":memory:") if self.path == ":memory:" else None
        with self._conn() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = self._mem or sqlite3.connect(self.path)
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            if self._mem is None:
                conn.close()

    # ------------------------------------------------------------ đọc
    def last_scan(self, project_id: str, exclude_scan: Optional[str] = None) -> Optional[dict[str, Any]]:
        with self._conn() as c:
            row = c.execute(
                "SELECT scan_id, created_at, drift_score, design_signature, package_signatures, actual_edges, "
                "strategy, stats FROM scans WHERE project_id=? AND scan_id != ? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (project_id, exclude_scan or "")).fetchone()
        if not row:
            return None
        return {"scan_id": row[0], "created_at": row[1], "drift_score": row[2], "design_signature": row[3],
                "package_signatures": json.loads(row[4] or "{}"),
                "actual_edges": {tuple(e) for e in json.loads(row[5] or "[]")},
                "strategy": json.loads(row[6] or "{}"), "stats": json.loads(row[7] or "{}")}

    def findings_of(self, project_id: str, scan_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute("SELECT data FROM findings WHERE project_id=? AND scan_id=?",
                             (project_id, scan_id)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def mappings(self, project_id: str) -> dict[str, list[dict]]:
        with self._conn() as c:
            rows = c.execute("SELECT design_id, packages, confidence, source, status FROM mappings WHERE project_id=?",
                             (project_id,)).fetchall()
        out: dict[str, list[dict]] = {}
        for did, pk, conf, src, st in rows:
            out.setdefault(did, []).append({"packages": json.loads(pk), "confidence": conf, "source": src, "status": st})
        return out

    def user_mappings(self, project_id: str) -> dict[str, list[str]]:
        return {d: m["packages"] for d, ms in self.mappings(project_id).items() for m in ms
                if m["source"] == "user" and m["status"] == "confirmed"}

    def rejected_pairs(self, project_id: str) -> set[tuple[str, str]]:
        return {(d, p) for d, ms in self.mappings(project_id).items() for m in ms
                if m["status"] == "rejected" for p in m["packages"]}

    def auto_mappings(self, project_id: str) -> dict[str, tuple[list[str], float]]:
        return {d: (m["packages"], m["confidence"]) for d, ms in self.mappings(project_id).items() for m in ms
                if m["source"] == "auto" and m["status"] in ("confirmed", "uncertain")}

    def decisions(self, project_id: str) -> dict[str, dict]:
        with self._conn() as c:
            rows = c.execute("SELECT fingerprint, decision, note FROM decisions WHERE project_id=?",
                             (project_id,)).fetchall()
        return {fp: {"decision": d, "note": n or ""} for fp, d, n in rows}

    def feedback_cases(self, project_id: str) -> list[dict]:
        """Finding đã được người dùng đánh giá (kèm nội dung finding mới nhất) -> case memory / few-shot."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT d.fingerprint, d.decision, d.note, f.data FROM decisions d "
                "JOIN findings f ON f.project_id = d.project_id AND f.fingerprint = d.fingerprint "
                "WHERE d.project_id=? ORDER BY f.rowid DESC", (project_id,)).fetchall()
        out: dict[str, dict] = {}
        for fp, decision, note, data in rows:
            if fp not in out:
                out[fp] = {**json.loads(data), "fingerprint": fp, "decision": decision, "note": note or ""}
        return list(out.values())

    def cache_get(self, key: str) -> Optional[str]:
        with self._conn() as c:
            row = c.execute("SELECT response FROM llm_cache WHERE cache_key=?", (key,)).fetchone()
        return row[0] if row else None

    def cache_put(self, key: str, response: str, model: str) -> None:
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?)", (key, response, model, _now()))

    # ------------------------------------------------------------ ghi
    def save_scan(self, project_id: str, scan_id: str, *, commit_hash: str, drift_score: float,
                  design_signature: str, package_signatures: dict[str, str], actual_edges: set[tuple[str, str]],
                  strategy: dict, stats: dict, findings: list[dict], auto_mappings: dict[str, tuple[list[str], float, str]]
                  ) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM scans WHERE project_id=? AND scan_id=?", (project_id, scan_id))
            c.execute("INSERT INTO scans VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (project_id, scan_id, _now(), commit_hash, drift_score, design_signature,
                       json.dumps(package_signatures), json.dumps(sorted(actual_edges)),
                       json.dumps(strategy, ensure_ascii=False), json.dumps(stats, ensure_ascii=False)))
            c.execute("DELETE FROM findings WHERE project_id=? AND scan_id=?", (project_id, scan_id))
            c.executemany("INSERT INTO findings VALUES (?,?,?,?)",
                          [(project_id, scan_id, f["fingerprint"], json.dumps(f, ensure_ascii=False)) for f in findings])
            c.execute("DELETE FROM mappings WHERE project_id=? AND source='auto'", (project_id,))
            c.executemany("INSERT OR REPLACE INTO mappings VALUES (?,?,?,?,?,?,?)",
                          [(project_id, d, json.dumps(p), conf, "auto", st, _now())
                           for d, (p, conf, st) in auto_mappings.items()])

    def record_decision(self, project_id: str, fingerprint: str, decision: str, note: str = "") -> None:
        if decision not in ("accepted", "false_positive"):
            raise ValueError("decision phải là 'accepted' hoặc 'false_positive'")
        with self._conn() as c:
            c.execute("INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?)",
                      (project_id, fingerprint, decision, note, _now()))

    def set_user_mapping(self, project_id: str, design_id: str, packages: list[str], status: str = "confirmed") -> None:
        if status not in ("confirmed", "rejected"):
            raise ValueError("status phải là 'confirmed' hoặc 'rejected'")
        with self._conn() as c:
            if status == "confirmed":
                c.execute("DELETE FROM mappings WHERE project_id=? AND design_id=? AND source='user' AND status='confirmed'",
                          (project_id, design_id))
            c.execute("INSERT OR REPLACE INTO mappings VALUES (?,?,?,?,?,?,?)",
                      (project_id, design_id, json.dumps(packages), 1.0, "user", status, _now()))
