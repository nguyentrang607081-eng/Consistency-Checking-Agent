"""CONFIDENCE SCORE: mỗi finding = tích của 4 yếu tố giải thích được.

    confidence = mapping * rule_clarity * evidence * data_completeness

  mapping           : độ chắc chắn của ánh xạ component (min của hai đầu; node chỉ có trong code = 0.9)
  rule_clarity      : độ rõ của luật dẫn tới kết luận (xem rule_engine.CLARITY)
  evidence          : sức mạnh bằng chứng trong code
                        - có câu lệnh import cụ thể (file + dòng), đích tồn tại     -> 1.0
                        - đích import không tồn tại file ("package ma")            -> 0.85
                        - kết luận dựa trên VẮNG MẶT import (absence)               -> 0.80
                          (phân tích import tĩnh không thấy được lời gọi HTTP/RPC/động)
  data_completeness : chất lượng dữ liệu đầu vào
                        - SCA: SUCCESS 1.0 / PARTIAL 0.8 / FAILED 0.5
                        - nhân với độ tin cậy trung bình của các node thiết kế liên quan (AEA gán)

Nếu confidence < confidence_threshold -> status "needs_review" (human-in-the-loop),
không kết luận chắc chắn.
"""
from __future__ import annotations

from ..schemas.outputs import ConfidenceBreakdown

EVIDENCE_IMPORT = 1.0
EVIDENCE_PHANTOM = 0.85
EVIDENCE_ABSENCE = 0.80
CODE_ONLY_NODE_MAPPING = 0.9
SCA_STATUS_FACTOR = {"SUCCESS": 1.0, "PARTIAL": 0.8, "FAILED": 0.5}

SEVERITY_WEIGHT = {"low": 0.25, "medium": 0.5, "high": 0.75, "critical": 1.0}


def score(mapping: float, rule_clarity: float, evidence: float, completeness: float) -> tuple[float, ConfidenceBreakdown]:
    b = ConfidenceBreakdown(mapping=round(mapping, 4), rule_clarity=round(rule_clarity, 4),
                            evidence=round(evidence, 4), data_completeness=round(completeness, 4))
    return round(mapping * rule_clarity * evidence * completeness, 4), b


def status_for(conf: float, threshold: float, undetermined: bool = False) -> str:
    if undetermined:
        return "undetermined"
    return "confirmed" if conf >= threshold else "needs_review"
