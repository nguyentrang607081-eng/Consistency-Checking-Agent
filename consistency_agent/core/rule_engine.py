"""RULE ENGINE: kiểm tra một phụ thuộc thực tế có hợp lệ theo luật kiến trúc không.

Thứ tự ưu tiên khi đánh giá phụ thuộc (S -> T) - dừng ở bước đầu tiên có kết luận:

  1. Luật tường minh (RuleSet.rules, kind forbid/allow). Nhiều luật khớp -> lấy luật CỤ THỂ nhất
     (id: 3 điểm, layer: 2, "*": 1, cộng cho cả hai đầu). Nếu allow và forbid cùng độ cụ thể
     -> XUNG ĐỘT -> "undetermined" (không tự suy diễn thành vi phạm).
  2. Luật layer (RuleSet.layers): layer trong phụ thuộc layer ngoài -> layer_violation.
     Chế độ strict: chỉ được phụ thuộc layer kề dưới.
  3. Luật suy ra từ thiết kế (nếu derive_rules_from_design):
       có cạnh S->T trong thiết kế  -> allowed
       có cạnh T->S (ngược chiều)    -> reverse_dependency (nghi vấn, độ rõ 0.7)
  4. Chính sách mặc định: design_only -> unexpected_dependency; allow -> allowed;
     unspecified -> undetermined.

Mỗi kết luận kèm ``clarity`` (độ rõ ràng của luật, 0..1) dùng cho confidence score.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..schemas.inputs import ArchitectureRule, RuleSet
from ..schemas.internal import DesignModel

CLARITY = {
    "explicit": 1.0,
    "layer": 0.95,
    "design_allowed": 0.85,
    "reverse": 0.70,
    "default_design_only": 0.65,
    "default_unspecified": 0.50,
    "conflict": 0.50,
}


@dataclass
class Verdict:
    verdict: str                 # allowed | forbidden | undetermined
    violation_type: Optional[str]
    rule_id: str
    clarity: float
    severity: Optional[str]
    description: str
    expected: str                # mô tả quan hệ mong đợi


def _plain(node_id: str) -> str:
    return node_id[5:] if node_id.startswith("code:") else node_id


class RuleEngine:
    def __init__(self, rules: RuleSet, design: DesignModel) -> None:
        self.rules = rules
        self.design = design
        self.layer_index = {spec.name: i for i, spec in enumerate(rules.layers)}
        self.layer_of: dict[str, str] = {}
        for spec in rules.layers:
            for c in spec.components:
                self.layer_of[c] = spec.name
        for cid, comp in design.components.items():
            if comp.layer and cid not in self.layer_of:
                self.layer_of[cid] = comp.layer
                self.layer_index.setdefault(comp.layer, len(self.layer_index))
        self.explicit = [r for r in rules.rules if r.kind in ("allow", "forbid")]
        self.required = [r for r in rules.rules if r.kind == "require"]

    # ------------------------------------------------------------ selector
    def layer(self, node_id: str) -> Optional[str]:
        return self.layer_of.get(node_id) or self.layer_of.get(_plain(node_id))

    def _match(self, selector: str, node_id: str) -> int:
        """Trả về độ cụ thể (0 = không khớp)."""
        if selector == "*":
            return 1
        if selector.startswith("layer:"):
            return 2 if self.layer(node_id) == selector[6:] else 0
        if selector.startswith("external:"):
            return 0
        if selector == node_id or selector == _plain(node_id):
            return 3
        return 0

    def _match_external(self, selector: str, ext: str) -> int:
        if selector.startswith("external:"):
            pref = selector[9:]
            return 3 if ext == pref or ext.startswith(pref + ".") else 0
        return 0

    def _resolve_explicit(self, candidates: list[tuple[int, ArchitectureRule]], s: str, t: str) -> Optional[Verdict]:
        if not candidates:
            return None
        top = max(c[0] for c in candidates)
        best = [r for spec, r in candidates if spec == top]
        kinds = {r.kind for r in best}
        if len(kinds) > 1:
            ids = ", ".join(r.rule_id for r in best)
            return Verdict("undetermined", None, ids, CLARITY["conflict"], "low",
                           f"Các luật {ids} mâu thuẫn (allow và forbid cùng mức cụ thể) cho {s} -> {t}.",
                           "không xác định (luật mâu thuẫn)")
        rule = best[0]
        if rule.kind == "allow":
            return Verdict("allowed", None, rule.rule_id, CLARITY["explicit"], None,
                           rule.description or f"Luật {rule.rule_id} cho phép {s} -> {t}.", f"{s} -> {t} (được phép)")
        return Verdict("forbidden", "forbidden_dependency", rule.rule_id, CLARITY["explicit"],
                       rule.severity or "high",
                       rule.description or f"Luật {rule.rule_id} cấm {s} -> {t}.", f"{s} -/-> {t} (bị cấm)")

    # ------------------------------------------------------------ API
    def evaluate(self, s: str, t: str) -> Verdict:
        ps, pt = _plain(s), _plain(t)
        # 1. luật tường minh
        cands = []
        for r in self.explicit:
            a, b = self._match(r.source, s), self._match(r.target, t)
            if a and b:
                cands.append((a + b, r))
        v = self._resolve_explicit(cands, ps, pt)
        if v:
            return v
        # 2. layer
        ls, lt = self.layer(s), self.layer(t)
        layer_ok: Optional[Verdict] = None
        if ls and lt and ls in self.layer_index and lt in self.layer_index:
            i, j = self.layer_index[ls], self.layer_index[lt]
            if i > j:
                return Verdict("forbidden", "layer_violation", f"LAYER:{ls}->{lt}", CLARITY["layer"], "high",
                               f"Layer '{ls}' (trong) phụ thuộc ngược lên layer '{lt}' (ngoài): {ps} -> {pt}.",
                               f"{ls} chỉ được phụ thuộc các layer bên trong")
            if self.rules.layer_policy == "strict" and j - i > 1:
                return Verdict("forbidden", "layer_violation", f"LAYER:{ls}->{lt}", CLARITY["layer"], "medium",
                               f"Bỏ qua layer trung gian (strict): {ps} ({ls}) -> {pt} ({lt}).",
                               f"{ls} chỉ được phụ thuộc layer kề dưới")
            # Đúng chiều layer: chỉ nói "không vi phạm layer"; vẫn xét tiếp thiết kế bên dưới.
            layer_ok = Verdict("allowed", None, f"LAYER:{ls}->{lt}", CLARITY["layer"], None,
                               "Đúng chiều layer.", f"{ls} -> {lt}")
        # 3. suy ra từ thiết kế
        g = self.design.graph
        if self.rules.derive_rules_from_design:
            if g.has_edge(s, t):
                return Verdict("allowed", None, f"DESIGN:{ps}->{pt}", CLARITY["design_allowed"], None,
                               "Có trong kiến trúc thiết kế.", f"{ps} -> {pt}")
            if layer_ok:
                return layer_ok
            if g.has_edge(t, s):
                return Verdict("forbidden", "reverse_dependency", f"DESIGN:{pt}->{ps}", CLARITY["reverse"], "medium",
                               f"Thiết kế quy định {pt} -> {ps}, nhưng code có chiều ngược lại {ps} -> {pt}.",
                               f"{pt} -> {ps} (một chiều)")
        elif layer_ok:
            return layer_ok
        # 4. chính sách mặc định
        policy = self.rules.default_policy
        if policy == "allow":
            return Verdict("allowed", None, "DEFAULT:allow", CLARITY["default_design_only"], None,
                           "Chính sách mặc định cho phép.", "(không ràng buộc)")
        if policy == "design_only":
            return Verdict("forbidden", "unexpected_dependency", "DEFAULT:design_only", CLARITY["default_design_only"],
                           "medium", f"Phụ thuộc {ps} -> {pt} không có trong kiến trúc thiết kế.",
                           "(không có trong thiết kế)")
        return Verdict("undetermined", "unexpected_dependency", "DEFAULT:unspecified", CLARITY["default_unspecified"],
                       "low", f"Phụ thuộc {ps} -> {pt} không có trong thiết kế và không có luật nào quy định.",
                       "(chưa có luật)")

    def evaluate_external(self, s: str, ext: str) -> Optional[Verdict]:
        """Chỉ luật tường minh với target 'external:...' mới áp dụng cho thư viện ngoài."""
        cands = []
        for r in self.explicit:
            a, b = self._match(r.source, s), self._match_external(r.target, ext)
            if a and b:
                cands.append((a + b, r))
        return self._resolve_explicit(cands, _plain(s), ext)

    def required_pairs(self, nodes: list[str]) -> list[tuple[str, str, ArchitectureRule]]:
        out: list[tuple[str, str, ArchitectureRule]] = []
        for r in self.required:
            srcs = [n for n in nodes if self._match(r.source, n)]
            tgts = [n for n in nodes if self._match(r.target, n)]
            out.extend((a, b, r) for a in srcs for b in tgts if a != b)
        return out

    def describe(self) -> dict:
        return {
            "explicit_rules": len(self.explicit),
            "require_rules": len(self.required),
            "layers": list(self.layer_index),
            "default_policy": self.rules.default_policy,
            "derived_from_design": self.rules.derive_rules_from_design,
        }
