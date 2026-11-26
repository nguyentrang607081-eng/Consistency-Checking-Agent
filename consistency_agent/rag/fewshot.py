"""FEW-SHOT: ngân hàng ví dụ có lời giải + bộ chọn ví dụ ĐỘNG cho từng finding.

* 19 ví dụ viết tay phủ 4 kịch bản, gồm cả các trường hợp BÁO NHẦM (verdict "reject") để reviewer
  học được khi nào không nên kết luận vi phạm (vd: lời gọi gRPC không hiện ra dưới dạng import,
  package scripts/tests không thuộc kiến trúc).
* Ví dụ sinh từ PHẢN HỒI NGƯỜI DÙNG (Knowledge): finding bị đánh dấu false_positive -> ví dụ "reject",
  được chấp nhận (accepted) -> ví dụ "confirm". Agent vì vậy học thêm sau mỗi lần phản hồi.
* Chọn ví dụ: điểm = BM25(finding, ví dụ) chuẩn hóa + 1.0 nếu cùng loại vi phạm + 0.5 nếu cùng chi tiết
  kịch bản + 2.0 nếu là phản hồi người dùng về chính component đó; lấy top-k (mặc định 3).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .retriever import BM25Index, Document


@dataclass
class FewShotExample:
    example_id: str
    input: dict
    output: dict
    origin: str = "curated"  # curated | feedback

    def input_text(self) -> str:
        i = self.input
        return (f"{i.get('violation_type')} {i.get('scenario_detail', '')} {i.get('source')} -> {i.get('target', '')}. "
                f"status {i.get('status')}. {i.get('description', '')} Bằng chứng: {i.get('evidence', '')}")


def _ex(eid: str, vtype: str, detail: str, source: str, target: str, status: str, description: str, evidence: str,
        scenario: str, verdict: str, conf: float, rationale: str, fix: str) -> FewShotExample:
    return FewShotExample(eid, {"violation_type": vtype, "scenario_detail": detail, "source": source,
                                "target": target, "status": status, "description": description,
                                "evidence": evidence},
                          {"scenario": scenario, "verdict": verdict, "confidence": conf, "rationale": rationale,
                           "suggested_fix": fix})


CURATED: list[FewShotExample] = [
    _ex("fs-match-1", "uncertain_mapping", "mapping", "payment", "paymnt_proc", "needs_review",
        "Ánh xạ 'Payment Processor' -> package 'paymnt_proc' chưa chắc (tên gần giống, structure 1.0).",
        "checkout -> paymnt_proc khớp cạnh thiết kế checkout -> payment",
        "match", "confirm", 0.8,
        "Tên viết tắt của Payment Processor và quan hệ phụ thuộc khớp chiều thiết kế: đây là cùng một component.",
        "Xác nhận ánh xạ bằng feedback confirm-mapping để lần sau dùng làm neo."),
    _ex("fs-match-2", "uncertain_mapping", "mapping", "engine", "zeta", "needs_review",
        "Ánh xạ 'Recommendation Engine' -> 'zeta' chỉ dựa trên cấu trúc, tên không liên quan.",
        "api -> zeta -> models khớp cạnh thiết kế", "uncertain", "uncertain", 0.5,
        "Chỉ có bằng chứng cấu trúc; không có gì trong tên hay nội dung module xác nhận zeta là Recommendation Engine.",
        "Mở các module trong zeta để kiểm tra, rồi xác nhận hoặc bác bỏ ánh xạ."),
    _ex("fs-match-3", "unexpected_dependency", "rule_unspecified", "api", "models", "undetermined",
        "Phụ thuộc api -> models không có trong thiết kế và chưa có luật.",
        "from models.book import Book (dùng làm kiểu dữ liệu trả về)", "match", "reject", 0.65,
        "API ở layer ngoài dùng entity ở layer trong là đúng quy tắc phụ thuộc hướng vào trong; thiết kế chỉ chưa vẽ cạnh này.",
        "Thêm cạnh api -> models vào tài liệu kiến trúc hoặc luật allow."),
    _ex("fs-uncertain-1", "unexpected_dependency", "rule_unspecified", "aggregation", "config", "undetermined",
        "Phụ thuộc aggregation -> config không có trong thiết kế và không có luật nào quy định.",
        "from ..config import CoordinatorConfig (internal)", "uncertain", "uncertain", 0.6,
        "Phụ thuộc có thật trong code nhưng thiết kế không vẽ và chưa có luật: không đủ căn cứ để gọi là vi phạm; "
        "đọc cấu hình là nhu cầu phổ biến nên nhiều khả năng tài liệu chỉ thiếu cạnh này.",
        "Người phụ trách kiến trúc quyết định: thêm cạnh vào thiết kế / luật allow, hoặc luật forbid nếu không mong muốn."),
    _ex("fs-wrong-1", "forbidden_dependency", "forbidden_dependency", "api", "repositories", "confirmed",
        "Luật R1 cấm API truy cập Repository trực tiếp.", "from repositories.book_repo import BookRepository",
        "wrong", "confirm", 0.95,
        "Có câu lệnh import cụ thể vi phạm luật tường minh: presentation gọi tắt qua layer application.",
        "Chuyển lời gọi BookRepository vào service và để API chỉ gọi service."),
    _ex("fs-wrong-2", "layer_violation", "layer_violation", "models", "services", "confirmed",
        "Layer domain phụ thuộc ngược lên layer application.",
        "from services.loan_service import LoanService (import trong hàm)", "wrong", "confirm", 0.9,
        "Domain không được biết tới service; import đặt trong hàm là dấu hiệu né chu trình import.",
        "Định nghĩa interface ở domain hoặc truyền dữ liệu vào entity thay vì import service."),
    _ex("fs-wrong-3", "reverse_dependency", "reverse_dependency", "notification_service", "order_service",
        "needs_review", "Thiết kế chỉ có Order -> Notification (Kafka), code có Notification -> Order.",
        "from orders.service import OrderService", "wrong", "confirm", 0.8,
        "Phụ thuộc ngược chiều thiết kế tạo chu trình giữa hai service; thiết kế yêu cầu giao tiếp một chiều bằng event.",
        "Gửi dữ liệu cần thiết trong event thay vì để Notification gọi ngược Order Service."),
    _ex("fs-wrong-4", "layer_violation", "layer_violation", "usecases", "dependency_injection", "confirmed",
        "Use case import di.unit_of_work (layer composition).", "from di.unit_of_work import AbstractUnitOfWork",
        "wrong", "confirm", 0.7,
        "Use case chỉ cần abstraction nhưng abstraction lại đặt trong module DI chứa driver cụ thể, nên vẫn kéo phụ thuộc ra layer ngoài.",
        "Chuyển AbstractUnitOfWork vào layer application; DI chỉ hiện thực và lắp ráp."),
    _ex("fs-missing-1", "missing_component", "component", "notifications", "", "needs_review",
        "Component 'Notifications' không tìm thấy trong mã nguồn, không có ứng viên gần.",
        "không có package nào chứa token notification/mail", "missing", "confirm", 0.8,
        "Không có package hay module nào tương ứng và không có ứng viên ánh xạ: component chưa được hiện thực.",
        "Hiện thực Notifications hoặc cập nhật tài liệu nếu đã bỏ tính năng."),
    _ex("fs-missing-2", "missing_dependency", "dependency", "api_gateway", "order_service", "needs_review",
        "Thiết kế có api_gateway -> order_service (sync_call gRPC) nhưng không thấy import.",
        "hai service triển khai riêng, giao tiếp qua mạng", "match", "reject", 0.6,
        "Quan hệ gRPC giữa hai service không thể hiện dưới dạng import; phân tích tĩnh không thấy được nên đây không phải thiếu sót.",
        "Bổ sung phân tích lời gọi mạng hoặc đánh dấu false_positive."),
    _ex("fs-missing-3", "missing_component", "component", "shared", "", "needs_review",
        "Component 'Shared Utilities' không tìm thấy. Ứng viên gần nhất: common (0.55).",
        "package common được nhiều component dùng", "uncertain", "reject", 0.6,
        "Có ứng viên 'common' đồng nghĩa với shared utilities; nhiều khả năng component tồn tại dưới tên khác.",
        "Xác nhận ánh xạ shared -> common bằng feedback confirm-mapping."),
    _ex("fs-missing-4", "missing_dependency", "dependency", "services", "models", "needs_review",
        "Thiết kế có services -> models (dependency) nhưng không thấy import tương ứng.",
        "cùng một ứng dụng Python, services chỉ import repositories", "missing", "confirm", 0.75,
        "Hai component cùng tiến trình nên quan hệ thiết kế phải hiện ra dưới dạng import; không có import nghĩa là "
        "service đang bỏ qua entity (xử lý dữ liệu thô).",
        "Cho services làm việc với entity trong models, hoặc cập nhật thiết kế nếu quan hệ đã bỏ."),
    _ex("fs-junk-5", "unexpected_dependency", "dependency_to_junk", "api", "cache", "needs_review",
        "Phụ thuộc api -> cache không có trong kiến trúc thiết kế.",
        "from src.cache.redis_client import cache -> cache.redis_client (unresolved_internal)", "junk", "confirm", 0.85,
        "Phụ thuộc trỏ tới package không tồn tại: là một phần của code rác, sẽ lỗi khi chạy.",
        "Xóa hoặc sửa import tới module không tồn tại."),
    _ex("fs-junk-6", "unexpected_dependency", "dependency_to_junk", "legacy", "models", "needs_review",
        "Phụ thuộc legacy -> models không có trong kiến trúc thiết kế.",
        "from models.book import Book (trong package legacy không ai dùng)", "junk", "confirm", 0.7,
        "Phụ thuộc xuất phát từ package ngoài kiến trúc không còn được gọi; nó biến mất khi xóa code chết.",
        "Xử lý cùng package nguồn: xóa nếu là code chết, hoặc đưa vào kiến trúc."),
    _ex("fs-junk-7", "unexpected_dependency", "dependency_to_junk", "services", "utils", "needs_review",
        "Phụ thuộc services -> utils không có trong kiến trúc thiết kế.",
        "from utils.text import normalize -> utils.text (internal)", "junk", "confirm", 0.7,
        "Component thiết kế đang dùng một package có thật nhưng tài liệu không mô tả; phụ thuộc này là hệ quả của "
        "package undocumented.",
        "Bổ sung utils vào tài liệu kiến trúc cùng cạnh services -> utils, hoặc gộp hàm vào services."),
    _ex("fs-junk-1", "unexpected_component", "orphan", "legacy", "", "confirmed",
        "Package 'legacy' không có trong kiến trúc; không component thiết kế nào dùng tới.",
        "module legacy.old_report chỉ import models, không ai import legacy", "junk", "confirm", 0.85,
        "Không có phụ thuộc đi vào package này từ kiến trúc: ứng viên code chết.",
        "Kiểm tra entry point/script; nếu không dùng thì xóa package."),
    _ex("fs-junk-2", "unexpected_component", "phantom", "infrastructure", "", "confirmed",
        "Package 'infrastructure' được import nhưng không tồn tại trong mã nguồn.",
        "from src.infrastructure.database import ... (unresolved_internal)", "junk", "confirm", 0.95,
        "Import tới module không tồn tại sẽ gây ImportError; đây là dấu vết refactor còn sót.",
        "Sửa đường dẫn import hoặc xóa import."),
    _ex("fs-junk-3", "unexpected_component", "orphan", "scripts", "", "confirmed",
        "Package 'scripts' không có trong kiến trúc; không component thiết kế nào dùng tới.",
        "scripts/seed_db.py chạy tay để tạo dữ liệu mẫu", "match", "reject", 0.7,
        "Script tiện ích chạy tay không thuộc kiến trúc ứng dụng; báo là code rác là báo nhầm.",
        "Thêm 'scripts' vào ignore_code_packages."),
    _ex("fs-junk-4", "unexpected_component", "undocumented", "utils", "", "confirmed",
        "Package 'utils' có trong code nhưng không được mô tả; services đang dùng.",
        "from utils.text import normalize", "junk", "confirm", 0.75,
        "Package thật sự được component thiết kế sử dụng nhưng tài liệu kiến trúc bỏ sót.",
        "Bổ sung utils vào tài liệu kiến trúc (component Shared) hoặc gộp vào services."),
]


def from_feedback(cases: Iterable[dict]) -> list[FewShotExample]:
    out = []
    for c in cases:
        verdict = "reject" if c["decision"] == "false_positive" else "confirm"
        scenario = "match" if verdict == "reject" else (c.get("scenario") or "wrong")
        out.append(FewShotExample(
            f"fb-{c['fingerprint'][:8]}",
            {"violation_type": c.get("violation_type"), "scenario_detail": c.get("scenario_detail", ""),
             "source": c.get("source_component"), "target": c.get("target_component"), "status": c.get("status"),
             "description": c.get("description", ""),
             "evidence": "; ".join(e.get("statement", "") for e in c.get("evidence", [])[:2])},
            {"scenario": scenario, "verdict": verdict, "confidence": 0.9,
             "rationale": f"Người dùng đã đánh dấu {c['decision']}" + (f": {c['note']}" if c.get("note") else "."),
             "suggested_fix": c.get("recommended_action", "")},
            origin="feedback"))
    return out


class FewShotSelector:
    def __init__(self, examples: list[FewShotExample]) -> None:
        self.examples = examples
        self.index = BM25Index([Document(e.example_id, "", e.input_text(), e.origin) for e in examples])

    def select(self, finding_input: dict, k: int = 3) -> list[tuple[FewShotExample, float]]:
        text = (f"{finding_input.get('violation_type')} {finding_input.get('scenario_detail', '')} "
                f"{finding_input.get('source')} -> {finding_input.get('target', '')}. {finding_input.get('description', '')} "
                f"{finding_input.get('evidence', '')}")
        raw = self.index.score(text)
        top = max(raw) if raw and max(raw) > 0 else 1.0
        scored = []
        for ex, s in zip(self.examples, raw):
            v = s / top
            if ex.input.get("violation_type") == finding_input.get("violation_type"):
                v += 1.0
            if ex.input.get("scenario_detail") and ex.input.get("scenario_detail") == finding_input.get("scenario_detail"):
                v += 0.5
            if ex.origin == "feedback":
                v += 0.01
                # phản hồi của người dùng về CHÍNH component này -> áp dụng cho các finding liên quan
                if ex.input.get("source") and ex.input.get("source") in (finding_input.get("source"),
                                                                         finding_input.get("target")):
                    v += 2.0
            scored.append((ex, round(v, 4)))
        scored.sort(key=lambda x: (-x[1], x[0].example_id))
        return scored[:k]
