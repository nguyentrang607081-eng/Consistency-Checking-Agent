"""So khớp TÊN giữa component thiết kế và package mã nguồn.

Tên chỉ là MỘT nguồn bằng chứng. Điểm tên không bao giờ một mình đủ để khẳng định
hai component là một (trừ khi trùng khớp tuyệt đối sau chuẩn hóa) - xem ``graph_matching``.

Các phương pháp (lấy điểm cao nhất, mỗi phương pháp có trần riêng):
  exact     1.00  trùng sau chuẩn hóa ("Use Cases" == "usecases", "order_service" == "OrderService")
  core      0.88  trùng sau khi bỏ hậu tố kỹ thuật ("Order Service" ~ "orders")
  head      0.80  một token của tên thiết kế trùng tên package ("REST Controllers" ~ "controllers")
  acronym   0.75  chữ viết tắt ("Dependency Injection" ~ "di")
  synonym   0.60  từ đồng nghĩa trong từ điển kiến trúc ("Domain Entities" ~ "models")
  fuzzy     <=0.70  difflib ratio / Jaccard token (chỉ là gợi ý)
"""
from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Từ không mang nghĩa phân biệt trong tên component
STOPWORDS = {"the", "a", "an", "of", "and", "layer", "module", "modules", "component", "components",
             "package", "packages", "system", "tang", "lop", "he", "thong"}
# Hậu tố kỹ thuật hay gặp, bỏ khi so khớp tên chính ("Order Service" ~ "orders")
WEAK_SUFFIX = {"service", "services", "svc", "manager", "impl", "server", "api"}

SYNONYMS: list[set[str]] = [
    {"model", "models", "entity", "entities", "domain"},
    {"controller", "controllers", "presentation", "handler", "handlers", "route", "routes", "router",
     "routers", "view", "views", "endpoint", "endpoints", "ui", "web", "frontend"},
    {"repository", "repositories", "repo", "repos", "dao", "persistence", "data", "storage", "store",
     "infrastructure", "infra"},
    {"database", "db", "orm", "sql"},
    {"common", "shared", "util", "utils", "utility", "utilities", "core", "helpers", "helper", "lib"},
    {"setting", "settings", "config", "configs", "configuration", "conf"},
    {"usecase", "usecases", "interactor", "interactors", "application", "business", "logic"},
    {"di", "container", "injection", "wiring", "bootstrap"},
    {"notification", "notifications", "notifier", "mail", "email", "messaging"},
    {"gateway", "gateways", "proxy", "edge"},
    {"order", "orders", "ordering"},
    {"payment", "payments", "billing"},
    {"user", "users", "account", "accounts", "auth", "identity"},
    {"queue", "broker", "kafka", "rabbitmq", "events", "event"},
    {"cache", "caching", "redis"},
]


def strip_accents(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).replace("đ", "d").replace("Đ", "D")


def tokens(text: str) -> list[str]:
    """'OrderService', 'order_service', 'Order Service' -> ['order', 'service']."""
    text = strip_accents(text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    parts = re.split(r"[^A-Za-z0-9]+", text.lower())
    return [p for p in parts if p and p not in STOPWORDS]


def stem(tok: str) -> str:
    if len(tok) > 4 and tok.endswith("ies"):
        return tok[:-3] + "y"
    if len(tok) > 3 and tok.endswith("s") and not tok.endswith("ss"):
        return tok[:-1]
    return tok


def _core(toks: list[str]) -> list[str]:
    core = [t for t in toks if t not in WEAK_SUFFIX]
    return core or toks


def _synonym(a: str, b: str) -> bool:
    return any(a in s and b in s for s in SYNONYMS)


def name_similarity(design_names: list[str], package: str) -> tuple[float, str]:
    """Điểm tên giữa (các tên của) một component thiết kế và một package dotted.

    Package được so theo đoạn cuối (``repositories.relational_db`` -> ``relational_db``).
    Trả về (điểm, phương_pháp).
    """
    last = package.split(".")[-1]
    p_toks = [stem(t) for t in tokens(last)]
    p_concat = "".join(p_toks)
    best, method = 0.0, "none"

    def take(score: float, m: str) -> None:
        nonlocal best, method
        if score > best:
            best, method = score, m

    for raw in design_names:
        d_all = tokens(raw)
        if not d_all or not p_toks:
            continue
        d_toks = [stem(t) for t in d_all]
        d_core = [stem(t) for t in _core(d_all)]
        if "".join(d_toks) == p_concat:
            take(1.0, "exact")
            continue
        if "".join(d_core) == p_concat:
            take(0.88, "core")  # khớp sau khi bỏ hậu tố kỹ thuật ("Order Service" ~ "orders")
        if p_concat in d_core or p_concat in d_toks:
            take(0.8, "head")
        initials = "".join(t[0] for t in d_all if t)
        if len(initials) >= 2 and initials == last.lower().replace("_", ""):
            take(0.75, "acronym")
        if any(_synonym(dt, pt) for dt in d_core for pt in p_toks):
            take(0.6, "synonym")
        ratio = SequenceMatcher(None, "".join(d_core), p_concat).ratio()
        jac = len(set(d_core) & set(p_toks)) / len(set(d_core) | set(p_toks))
        take(round(0.7 * max(ratio, jac), 4), "fuzzy")
    return round(best, 4), method


def package_depth(package: str) -> int:
    return package.count(".") + 1
