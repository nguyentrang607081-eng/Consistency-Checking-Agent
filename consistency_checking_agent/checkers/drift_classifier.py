"""Optional LLM drift classification hook. Deterministic mode is the default."""
import os
import hashlib
import json
import sqlite3
from pathlib import Path

class DriftClassifier:
    def __init__(self, enabled: bool | None = None, cache_path: str | None = None, client=None):
        self.enabled = (os.getenv("ENABLE_LLM", "false").lower() == "true") if enabled is None else enabled
        self.cache_path = cache_path or os.getenv("LLM_CACHE_PATH", ".cache/llm_cache.db")
        self.client = client
        Path(self.cache_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache_path) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS llm_cache (cache_key TEXT PRIMARY KEY, verdict TEXT NOT NULL, confidence REAL NOT NULL)")

    @staticmethod
    def _key(candidate: dict) -> str:
        raw = json.dumps({k: candidate.get(k) for k in ("source_id", "target_id", "evidence")}, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def classify(self, candidate: dict, threshold: float = .75):
        if not self.enabled or self.client is None:
            return None
        key = self._key(candidate)
        with sqlite3.connect(self.cache_path) as conn:
            row = conn.execute("SELECT verdict, confidence FROM llm_cache WHERE cache_key=?", (key,)).fetchone()
        if row:
            return {"verdict": row[0], "confidence": row[1], "cache_hit": True}
        # Provider-specific prompting is intentionally delegated to a compatible client adapter.
        try:
            response = await self.client.classify_drift(candidate)
            verdict = str(response["verdict"]).lower()
            confidence = float(response["confidence"])
            if verdict not in {"intentional", "accidental"} or not 0 <= confidence <= 1:
                raise ValueError("Invalid drift classifier response")
            with sqlite3.connect(self.cache_path) as conn:
                conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?, ?, ?)", (key, verdict, confidence))
            return {"verdict": verdict, "confidence": confidence, "cache_hit": False} if confidence >= threshold else None
        except Exception:
            return None
