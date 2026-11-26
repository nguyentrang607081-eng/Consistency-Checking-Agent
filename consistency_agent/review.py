"""REVIEW: rà soát finding bằng RAG + few-shot + LLM (ý kiến thứ hai).

Với mỗi finding cần rà soát:
  1. RAG      : truy hồi top-k tài liệu (BM25) từ corpus kiến trúc + bằng chứng thiết kế (AEA) + luật dự án
                + case memory (phản hồi người dùng trong Knowledge).
  2. Few-shot : chọn top-k ví dụ có lời giải gần nhất (gồm ví dụ sinh từ phản hồi người dùng).
  3. Reviewer : LLM (prompt = system + cặp few-shot + ngữ cảnh RAG + finding, trả JSON) hoặc bộ suy luận
                few-shot cục bộ khi không có LLM. LLM lỗi / trả JSON sai -> tự động dùng bộ cục bộ.
  4. Policy   : "advisory" (mặc định) chỉ ghi ý kiến vào finding.llm_review;
                "resolve" cho phép LLM THẬT nâng finding needs_review -> confirmed khi verdict=confirm và
                confidence >= 0.75. LLM KHÔNG BAO GIỜ tự gỡ bỏ một finding (reject chỉ là khuyến nghị):
                bác bỏ vi phạm là quyền của người dùng (feedback false-positive).

Chi phí: chỉ gọi LLM trong giới hạn ``llm_budget``; câu trả lời được cache theo hash(prompt) trong SQLite.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .adaptive.knowledge import KnowledgeStore
from .llm.providers import (LocalFewShotReasoner, Reviewer, ReviewRequest, build_messages, prompt_hash)
from .rag.fewshot import CURATED, FewShotSelector, from_feedback
from .rag.retriever import BM25Index, build_knowledge_base
from .scenarios import LABEL_VI
from .schemas.outputs import Finding, LLMReview, ReviewMetadata, ScenarioReport

log = logging.getLogger(__name__)
LOW_CONF_STATUSES = {"needs_review", "undetermined"}
SEV_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}
RESOLVE_MIN_CONF = 0.75


def finding_input(f: Finding) -> dict:
    return {"violation_type": f.violation_type, "scenario_detail": f.scenario_detail,
            "source": f.source_component, "target": f.target_component, "status": f.status,
            "severity": f.severity, "confidence_score": f.confidence_score, "rule_id": f.rule_id,
            "description": f.description, "expected": f.expected_dependency, "actual": f.actual_dependency,
            "evidence": "; ".join(e.statement or e.file_path for e in f.evidence[:3])}


class ReviewEngine:
    def __init__(self, reviewer: Reviewer, kb: Optional[KnowledgeStore] = None, policy: str = "advisory",
                 budget: int = 20, k_docs: int = 4, k_shots: int = 3) -> None:
        self.reviewer = reviewer
        self.local = LocalFewShotReasoner()
        self.kb = kb
        self.policy = policy
        self.budget = budget
        self.k_docs, self.k_shots = k_docs, k_shots
        self._mem_cache: dict[str, str] = {}
        self._llm_down = False  # True khi không kết nối được LLM -> dùng bộ cục bộ cho phần còn lại

    # ------------------------------------------------------------ cache
    def _cache_get(self, key: str) -> Optional[str]:
        return self.kb.cache_get(key) if self.kb else self._mem_cache.get(key)

    def _cache_put(self, key: str, value: str) -> None:
        if self.kb:
            self.kb.cache_put(key, value, self.reviewer.model)
        else:
            self._mem_cache[key] = value

    # ------------------------------------------------------------ run
    def run(self, findings: list[Finding], design, rules, project_id: str, scope: str = "all") -> ReviewMetadata:
        meta = ReviewMetadata(mode="llm" if self.reviewer.is_llm else "local", provider=self.reviewer.name,
                              model=self.reviewer.model, policy=self.policy)  # type: ignore[arg-type]
        cases = self.kb.feedback_cases(project_id) if self.kb else []
        docs = build_knowledge_base(design, rules, cases)
        index = BM25Index(docs)
        examples = CURATED + from_feedback(cases)
        selector = FewShotSelector(examples)
        meta.rag_documents, meta.few_shot_pool = len(docs), len(examples)

        targets = [f for f in findings if f.status != "accepted" and not f.carried_over]
        if scope == "low_confidence":
            targets = [f for f in targets if f.status in LOW_CONF_STATUSES or f.violation_type == "uncertain_mapping"]
        targets.sort(key=lambda f: (f.status not in LOW_CONF_STATUSES and f.violation_type != "uncertain_mapping",
                                    SEV_RANK[f.severity], f.finding_id))
        for f in targets:
            fin = finding_input(f)
            query = f"{LABEL_VI.get(f.scenario or '', '')} {f.violation_type} {f.scenario_detail} " \
                    f"{f.source_component} {f.target_component} {f.description} {fin['evidence']}"
            req = ReviewRequest(fin, f.scenario or "uncertain", index.search(query, self.k_docs),
                                selector.select(fin, self.k_shots))
            out, provider, model, is_llm, cached = None, self.reviewer.name, self.reviewer.model, self.reviewer.is_llm, False
            if self.reviewer.is_llm:
                key = prompt_hash(self.reviewer.model, build_messages(req))
                hit = self._cache_get(key)
                if hit is not None:
                    out, cached = json.loads(hit), True
                    meta.cache_hits += 1
                elif meta.llm_calls < self.budget and not self._llm_down:
                    try:
                        meta.llm_calls += 1
                        out = self.reviewer.review(req)
                        self._cache_put(key, json.dumps(out, ensure_ascii=False))
                    except (RuntimeError, ValueError, KeyError, json.JSONDecodeError) as exc:
                        meta.fallbacks += 1
                        if "Không kết nối" in str(exc):
                            self._llm_down = True  # ngắt mạch: không thử lại cho các finding còn lại
                        if len(meta.errors) < 5:
                            meta.errors.append(f"{f.finding_id}: {exc}")
                        log.warning("LLM lỗi cho %s, dùng local: %s", f.finding_id, exc)
            if out is None:  # không dùng LLM, hết ngân sách hoặc LLM lỗi
                if self.reviewer.is_llm:
                    meta.local_fallback_reviews += 1
                out = self.local.review(req)
                provider, model, is_llm = self.local.name, self.local.model, False
            review = LLMReview(provider=provider, model=model, is_llm=is_llm, verdict=out["verdict"],
                               scenario=out["scenario"], confidence=out["confidence"], rationale=out["rationale"],
                               suggested_fix=out.get("suggested_fix", ""),
                               retrieved_docs=[d.doc_id for d, _ in req.documents],
                               cited_docs=out.get("cited_docs", []),
                               few_shot_ids=[e.example_id for e, _ in req.examples], cached=cached)
            if (self.policy == "resolve" and is_llm and f.status == "needs_review" and review.verdict == "confirm"
                    and review.confidence >= RESOLVE_MIN_CONF):
                f.status = "confirmed"
                review.status_changed = True
                meta.status_changes += 1
            if review.verdict == "reject" or (review.verdict == "confirm" and review.scenario != f.scenario):
                meta.disagreements += 1  # reviewer không đồng ý với kết luận tất định -> đáng xem lại
            f.llm_review = review
            meta.reviewed += 1
        return meta

    def summarize(self, rep: ScenarioReport, findings: list[Finding], meta: ReviewMetadata) -> None:
        """Dùng LLM viết tóm tắt (nếu còn ngân sách); ngược lại giữ tóm tắt theo mẫu."""
        if not self.reviewer.is_llm or meta.llm_calls >= self.budget or self._llm_down:
            return
        facts = {"verdict": rep.verdict, "primary_scenario": rep.primary_scenario, "counts": rep.counts,
                 "match_ratio": rep.match_ratio,
                 "items": [{"id": f.finding_id, "scenario": f.scenario, "type": f.violation_type,
                            "source": f.source_component, "target": f.target_component, "status": f.status}
                           for f in findings if f.status != "accepted"][:20]}
        try:
            meta.llm_calls += 1
            text = self.reviewer.complete_text(
                "Bạn viết tóm tắt kết quả kiểm tra kiến trúc cho sinh viên. Chỉ dùng dữ kiện được cho, "
                "không thêm số liệu. Tối đa 4 câu tiếng Việt, nêu kịch bản chính và việc nên làm đầu tiên.",
                json.dumps(facts, ensure_ascii=False))
            if text.strip():
                rep.summary, rep.summary_by = text.strip()[:1200], self.reviewer.model
        except (RuntimeError, KeyError) as exc:
            meta.fallbacks += 1
            meta.errors.append(f"summary: {exc}")

