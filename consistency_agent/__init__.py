"""Consistency Checking Agent - kiểm tra tính nhất quán giữa kiến trúc thiết kế và mã nguồn.

Một agent trong hệ thống Adaptive Multi-Agent (5 agent) của đề tài
"Nghiên cứu Adaptive Multi-Agent nhằm đảm bảo tính nhất quán giữa kiến trúc phần mềm và mã nguồn".
"""
try:  # .env là tùy chọn (pip install python-dotenv)
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

from .agent import ConsistencyCheckingAgent  # noqa: E402
from .config import AgentConfig  # noqa: E402

__all__ = ["ConsistencyCheckingAgent", "AgentConfig"]
__version__ = "1.0.0"
