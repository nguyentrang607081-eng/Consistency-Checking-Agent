"""Reviewer cho finding: LLM (API tương thích OpenAI) hoặc bộ suy luận few-shot CỤC BỘ.

Provider (biến môi trường CCA_LLM_PROVIDER):
  none          : tắt review.
  local         : (mặc định) KHÔNG phải LLM. Bỏ phiếu có trọng số trên các ví dụ few-shot gần nhất + giải
                  thích bằng tài liệu RAG. Chạy offline, không cần key - dùng để demo/so sánh.
  groq          : https://api.groq.com/openai/v1, model mặc định llama-3.3-70b-versatile, key GROQ_API_KEY
                  (cùng cấu hình với Architecture Extraction Agent của nhóm; Groq có gói miễn phí).
  ollama        : http://localhost:11434/v1 - chạy model mở trên máy (miễn phí, không cần key).
  openai        : https://api.openai.com/v1, key OPENAI_API_KEY (trả phí).
  openai_compat : endpoint bất kỳ theo chuẩn /chat/completions (CCA_LLM_BASE_URL).

Không dùng SDK: gọi HTTP bằng urllib (thư viện chuẩn) -> không thêm dependency.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol

from ..rag.fewshot import FewShotExample
from ..rag.retriever import Document

log = logging.getLogger(__name__)

PRESETS = {
    "groq": ("https://api.groq.com/openai/v1", "llama-3.3-70b-versatile", "GROQ_API_KEY"),
    "ollama": ("http://localhost:11434/v1", "qwen2.5:7b-instruct", ""),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OPENAI_API_KEY"),
    "openai_compat": ("", "", ""),
}
VALID_SCENARIOS = {"match", "wrong", "missing", "junk", "uncertain"}
VALID_VERDICTS = {"confirm", "reject", "uncertain"}

SYSTEM_PROMPT = """Bạn là chuyên gia kiểm tra tính nhất quán giữa kiến trúc phần mềm và mã nguồn.
Một bộ kiểm tra tự động (Reflexion Model + Rule Engine) đã tạo ra một FINDING. Nhiệm vụ: đánh giá lại finding đó.

Xếp finding vào đúng 1 trong 5 kịch bản:
- match: thực ra khớp thiết kế (finding là báo nhầm, hoặc ánh xạ component là đúng)
- wrong: code SAI so với thiết kế (phụ thuộc bị cấm / ngược layer / ngược chiều / chu trình)
- missing: code THIẾU so với thiết kế (component hoặc phụ thuộc thiết kế không có trong code)
- junk: code RÁC - có trong code nhưng thiết kế không có (code chết, package ngoài kiến trúc, import tới module không tồn tại)
- uncertain: chưa đủ bằng chứng

verdict: "confirm" (finding đúng), "reject" (báo nhầm), "uncertain" (không đủ thông tin).
CHỈ dựa vào finding, bằng chứng và TÀI LIỆU THAM KHẢO được cung cấp; trích id tài liệu đã dùng.
Không bịa file, dòng code hay luật không có trong dữ liệu. Nếu thiếu thông tin hãy trả "uncertain".
Trả về DUY NHẤT một đối tượng JSON:
{"scenario": "...", "verdict": "...", "confidence": 0.0-1.0, "rationale": "<= 3 câu tiếng Việt",
 "suggested_fix": "1 câu", "cited_docs": ["id", ...]}"""


@dataclass
class ReviewRequest:
    finding_input: dict
    deterministic_scenario: str
    documents: list[tuple[Document, float]] = field(default_factory=list)
    examples: list[tuple[FewShotExample, float]] = field(default_factory=list)


class Reviewer(Protocol):
    name: str
    model: str
    is_llm: bool

    def review(self, req: ReviewRequest) -> dict: ...

    def complete_text(self, system: str, user: str) -> str: ...


# ---------------------------------------------------------------- prompt
def build_messages(req: ReviewRequest) -> list[dict[str, str]]:
    """System + few-shot (cặp user/assistant) + câu hỏi kèm ngữ cảnh RAG."""
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for ex, _ in req.examples:
        msgs.append({"role": "user", "content": "FINDING:\n" + json.dumps(ex.input, ensure_ascii=False)})
        msgs.append({"role": "assistant", "content": json.dumps(ex.output, ensure_ascii=False)})
    context = "\n".join(f"[{d.doc_id}] {d.title}: {d.text}" for d, _ in req.documents) or "(không có)"
    msgs.append({"role": "user", "content":
                 f"TÀI LIỆU THAM KHẢO:\n{context}\n\nKịch bản do quy tắc tất định gán: {req.deterministic_scenario}\n\n"
                 f"FINDING:\n{json.dumps(req.finding_input, ensure_ascii=False)}"})
    return msgs


def prompt_hash(model: str, msgs: list[dict]) -> str:
    return hashlib.sha256((model + json.dumps(msgs, ensure_ascii=False, sort_keys=True)).encode()).hexdigest()


def parse_review(text: str, allowed_docs: set[str]) -> dict:
    """Trích JSON từ câu trả lời của LLM và chuẩn hóa. Ném ValueError nếu không hợp lệ."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError("LLM không trả về JSON")
    data = json.loads(m.group(0))
    scenario = str(data.get("scenario", "")).strip().lower()
    verdict = str(data.get("verdict", "")).strip().lower()
    if scenario not in VALID_SCENARIOS or verdict not in VALID_VERDICTS:
        raise ValueError(f"scenario/verdict không hợp lệ: {scenario}/{verdict}")
    try:
        conf = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    cited = [str(c) for c in data.get("cited_docs", []) if str(c) in allowed_docs]  # bỏ id bịa
    return {"scenario": scenario, "verdict": verdict, "confidence": min(1.0, max(0.0, conf)),
            "rationale": str(data.get("rationale", ""))[:800], "suggested_fix": str(data.get("suggested_fix", ""))[:400],
            "cited_docs": cited}


# ---------------------------------------------------------------- local
class LocalFewShotReasoner:
    """Bộ suy luận cục bộ, KHÔNG phải LLM: kNN có trọng số trên ví dụ few-shot + giải thích từ RAG."""

    name = "local-fewshot"
    model = "knn-fewshot-v1"
    is_llm = False

    def review(self, req: ReviewRequest) -> dict:
        if not req.examples:
            return {"scenario": req.deterministic_scenario, "verdict": "uncertain", "confidence": 0.0,
                    "rationale": "Không có ví dụ tương tự.", "suggested_fix": "", "cited_docs": []}
        votes: dict[str, float] = {}
        for ex, w in req.examples:
            votes[ex.output["verdict"]] = votes.get(ex.output["verdict"], 0.0) + w
        verdict = max(votes, key=lambda v: (votes[v], v == "confirm"))
        share = votes[verdict] / sum(votes.values())
        same_verdict = [ex for ex, _ in req.examples if ex.output["verdict"] == verdict]
        vtype = req.finding_input.get("violation_type")
        # giải thích bằng ví dụ CÙNG loại vi phạm nếu có, tránh mượn lời giải không liên quan
        detail = req.finding_input.get("scenario_detail")
        best = next((ex for ex in same_verdict if ex.input.get("violation_type") == vtype
                     and ex.input.get("scenario_detail") == detail),
                    next((ex for ex in same_verdict if ex.input.get("violation_type") == vtype), same_verdict[0]))
        scenario = req.deterministic_scenario if verdict == "confirm" else (
            best.output["scenario"] if verdict == "reject" else "uncertain")
        docs = [d for d, _ in req.documents[:2]]
        cite = "; ".join(f"[{d.doc_id}] {d.title}" for d in docs)
        rationale = (f"Gần nhất với ví dụ {best.example_id} ({best.output['verdict']}): {best.output['rationale']}"
                     + (f" Tài liệu liên quan: {cite}." if cite else ""))
        fix_doc = next((d for d, _ in req.documents if d.doc_id.startswith("remediation#")), None)
        fix = best.output.get("suggested_fix") or (fix_doc.text if fix_doc else "")
        return {"scenario": scenario, "verdict": verdict, "confidence": round(share, 4), "rationale": rationale,
                "suggested_fix": fix, "cited_docs": [d.doc_id for d in docs]}

    def complete_text(self, system: str, user: str) -> str:  # pragma: no cover - không dùng
        raise NotImplementedError


# ---------------------------------------------------------------- HTTP LLM
class OpenAICompatLLM:
    is_llm = True

    def __init__(self, base_url: str, model: str, api_key: str = "", timeout: float = 30.0,
                 name: str = "openai_compat") -> None:
        if not base_url or not model:
            raise ValueError("Cần CCA_LLM_BASE_URL và CCA_LLM_MODEL cho provider openai_compat")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.name = name

    def _chat(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0,
                           "max_tokens": 600}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload: Any = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"LLM HTTP {exc.code}: {exc.read()[:200]!r}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError(f"Không kết nối được LLM ({self.base_url}): {exc}") from exc
        return payload["choices"][0]["message"]["content"]

    def review(self, req: ReviewRequest) -> dict:
        text = self._chat(build_messages(req))
        return parse_review(text, {d.doc_id for d, _ in req.documents})

    def complete_text(self, system: str, user: str) -> str:
        return self._chat([{"role": "system", "content": system}, {"role": "user", "content": user}])


def create_reviewer(provider: Optional[str] = None, model: Optional[str] = None, base_url: Optional[str] = None,
                    api_key: Optional[str] = None, timeout: Optional[float] = None) -> Optional[Reviewer]:
    provider = (provider or os.getenv("CCA_LLM_PROVIDER") or "local").strip().lower()
    if provider in ("none", "off", ""):
        return None
    if provider == "local":
        return LocalFewShotReasoner()
    if provider not in PRESETS:
        raise ValueError(f"CCA_LLM_PROVIDER không hợp lệ: {provider}")
    p_url, p_model, key_env = PRESETS[provider]
    key = api_key or os.getenv("CCA_LLM_API_KEY") or (os.getenv(key_env) if key_env else "") or ""
    if key_env and not key:
        raise ValueError(f"Provider '{provider}' cần API key: đặt {key_env} hoặc CCA_LLM_API_KEY")
    return OpenAICompatLLM(base_url or os.getenv("CCA_LLM_BASE_URL") or p_url,
                           model or os.getenv("CCA_LLM_MODEL") or p_model, key,
                           timeout or float(os.getenv("CCA_LLM_TIMEOUT", "30")), provider)
