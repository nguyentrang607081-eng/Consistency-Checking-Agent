"""Cấu hình CCA. Giá trị mặc định có thể ghi đè bằng biến môi trường (xem .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


@dataclass
class AgentConfig:
    kb_path: str = field(default_factory=lambda: os.getenv("CCA_KB_PATH", "data/cca_knowledge.sqlite"))
    # Ngưỡng confidence để finding được "confirmed" (đồng bộ với confidence_threshold=0.7 của Coordinator)
    confidence_threshold: float = field(default_factory=lambda: _f("CCA_CONFIDENCE_THRESHOLD", 0.7))
    # Ngưỡng để ánh xạ component được chấp nhận tự động
    mapping_accept_threshold: float = field(default_factory=lambda: _f("CCA_MAPPING_ACCEPT", 0.75))
    mapping_candidate_threshold: float = 0.40
    mapping_margin: float = 0.08
    # Ngưỡng "đồ thị lớn" cho Plan (số module code / số component thiết kế)
    large_code_modules: int = field(default_factory=lambda: _i("CCA_LARGE_MODULES", 300))
    large_design_components: int = 40
    # Tỷ lệ package thay đổi tối đa để còn quét incremental
    incremental_max_change_ratio: float = 0.30
    # Tỷ lệ component có tên mơ hồ để bật deep matching
    ambiguity_deep_ratio: float = 0.30
    # Tỷ lệ finding tin cậy thấp ở lần trước để bật deep matching
    low_conf_deep_ratio: float = 0.30
    # ---- RAG + few-shot + LLM review
    # none | local (mặc định, offline, không phải LLM) | groq | ollama | openai | openai_compat
    llm_provider: str = field(default_factory=lambda: os.getenv("CCA_LLM_PROVIDER", "local"))
    # advisory: chỉ ghi ý kiến; resolve: LLM thật được nâng needs_review -> confirmed
    llm_policy: str = field(default_factory=lambda: os.getenv("CCA_LLM_POLICY", "advisory"))
    llm_max_calls: int = field(default_factory=lambda: _i("CCA_LLM_MAX_CALLS", 20))
    ignore_patterns: list[str] | None = None  # None -> dùng DEFAULT_IGNORE của code_adapter
    source_roots: list[str] | None = None
