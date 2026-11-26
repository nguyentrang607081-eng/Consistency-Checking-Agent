"""RAG - Retrieval: kho tài liệu + bộ truy hồi BM25 (viết bằng Python thuần, không thêm thư viện).

Nguồn tài liệu (``build_knowledge_base``):
  1. corpus/*.md          : tri thức kiến trúc tĩnh (4 kịch bản, Clean Architecture, layer, Reflexion,
                            erosion/drift, mẫu khắc phục, giới hạn phân tích tĩnh) - chia theo mục "## ".
  2. design:<id>          : bằng chứng văn bản của node thiết kế do Architecture Extraction Agent trích ra.
  3. rule:<id>            : mô tả luật kiến trúc của dự án.
  4. case:<fingerprint>   : "case memory" - các finding người dùng đã đánh giá (accepted / false_positive)
                            lấy từ Knowledge (SQLite) -> agent học từ phản hồi.

BM25 (k1=1.5, b=0.75) trên token đã bỏ dấu tiếng Việt, tách camelCase/snake_case (dùng lại naming.tokens).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ..core.naming import strip_accents

CORPUS_DIR = Path(__file__).resolve().parent / "corpus"
_STOP = {"va", "la", "cua", "cac", "co", "khong", "duoc", "cho", "de", "voi", "trong", "mot", "nhung", "khi",
         "thi", "nay", "do", "tu", "the", "a", "an", "of", "to", "in", "is", "and", "or", "nen", "se", "neu"}


def rag_tokens(text: str) -> list[str]:
    text = strip_accents(text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text).lower()
    return [t for t in re.split(r"[^a-z0-9]+", text) if len(t) > 1 and t not in _STOP]


@dataclass
class Document:
    doc_id: str
    title: str
    text: str
    source: str  # corpus | design | rule | case
    meta: dict = field(default_factory=dict)


class BM25Index:
    def __init__(self, docs: list[Document], k1: float = 1.5, b: float = 0.75) -> None:
        self.docs = docs
        self.k1, self.b = k1, b
        self.tf = [Counter(rag_tokens(d.title + " " + d.text)) for d in docs]
        self.len = [sum(tf.values()) for tf in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 0.0
        df: Counter = Counter()
        for tf in self.tf:
            df.update(tf.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: str) -> list[float]:
        q = rag_tokens(query)
        out = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            for t in q:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avg or 1)))
            out.append(s)
        return out

    def search(self, query: str, k: int = 4, sources: Optional[set[str]] = None) -> list[tuple[Document, float]]:
        scored = [(d, s) for d, s in zip(self.docs, self.score(query))
                  if s > 0 and (sources is None or d.source in sources)]
        scored.sort(key=lambda x: (-x[1], x[0].doc_id))
        return scored[:k]


def load_corpus(directory: Path = CORPUS_DIR) -> list[Document]:
    docs: list[Document] = []
    for path in sorted(directory.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        title_m = re.search(r"^# (.+)$", text, re.M)
        doc_title = title_m.group(1).strip() if title_m else path.stem
        for section in re.split(r"^## ", text, flags=re.M)[1:]:
            heading, _, body = section.partition("\n")
            slug = re.sub(r"[^a-z0-9]+", "-", strip_accents(heading).lower()).strip("-")
            docs.append(Document(f"{path.stem}#{slug}", f"{doc_title} - {heading.strip()}",
                                 " ".join(body.split()), "corpus"))
    return docs


def build_knowledge_base(design=None, rules=None, cases: Iterable[dict] = ()) -> list[Document]:
    docs = load_corpus()
    if design is not None:
        for comp in design.components.values():
            ev = getattr(comp, "evidence", None)
            text = f"Component {comp.name} ({comp.id}) loại {comp.type}" + (f", layer {comp.layer}" if comp.layer else "")
            if ev:
                text += f". Tài liệu: {ev}"
            docs.append(Document(f"design:{comp.id}", f"Thiết kế: {comp.name}", text, "design"))
    if rules is not None:
        for r in rules.rules:
            docs.append(Document(f"rule:{r.rule_id}", f"Luật {r.rule_id} ({r.kind})",
                                 f"{r.kind} {r.source} -> {r.target}. {r.description}", "rule"))
        for i, spec in enumerate(rules.layers):
            docs.append(Document(f"rule:layer:{spec.name}", f"Layer {spec.name}",
                                 f"Layer {spec.name} (thứ tự {i} từ ngoài vào trong) gồm {', '.join(spec.components)}.",
                                 "rule"))
    for c in cases:
        docs.append(Document(
            f"case:{c['fingerprint']}", f"Phản hồi người dùng: {c['decision']}",
            f"Finding {c.get('violation_type', '')} {c.get('source_component', '')} -> {c.get('target_component', '')}: "
            f"{c.get('description', '')}. Người dùng đánh dấu {c['decision']}. {c.get('note', '')}",
            "case", {"decision": c["decision"]}))
    return docs
