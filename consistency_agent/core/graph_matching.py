"""GRAPH MATCHING: ánh xạ component thiết kế <-> package mã nguồn (bước "mapping" của Reflexion Model).

Thuật toán (lặp kiểu "similarity propagation" rút gọn):

  Vòng 0 - Neo (anchor): ánh xạ khai báo (RuleSet.component_mapping, properties.code_paths của AEA)
           và ánh xạ người dùng đã xác nhận trong Knowledge -> confidence 1.0.
  Vòng k - Với mỗi cặp (D, P) chưa neo, tính:
       name(D,P)   : điểm tên (core/naming.py), có phạt nhẹ theo độ sâu package
       struct(D,P) : tỷ lệ cạnh thiết kế của D (nối tới các component ĐÃ NEO) mà trong code
                     cũng có phụ thuộc cùng chiều giữa P và package của component kia.
                     Không có láng giềng đã neo -> struct = None (không có bằng chứng cấu trúc).
       conf(D,P)   = 1 - (1 - 0.85*name) * (1 - 0.70*struct)      (noisy-OR, struct=None -> 0)
     Gán tham lam theo conf giảm dần (mỗi D một P, mỗi P tối đa một D).
     Cặp có conf >= accept_threshold VÀ cách biệt với ứng viên thứ hai >= margin -> "confirmed",
     trở thành NEO CỨNG (trọng số 1.0) cho vòng sau; cặp "uncertain" thành NEO MỀM (trọng số = conf).
     Nhờ vậy bằng chứng cấu trúc lan dần sang các component còn lại (similarity propagation).

Vì sao như vậy:
  * Tên trùng tuyệt đối mà không có cấu trúc: 0.85 -> chấp nhận (không phải "gần giống").
  * Tên chỉ gần giống (head 0.8 / acronym 0.75 / synonym 0.6 / fuzzy <=0.7) mà KHÔNG có cấu trúc:
    tối đa 0.68 < 0.75 -> trạng thái "uncertain", cần người xác nhận.
  * Chỉ có cấu trúc, tên không liên quan: tối đa 0.70 -> "uncertain".
  => Không bao giờ tự động khẳng định chỉ vì tên gần giống.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from ..schemas.internal import CodeModel, DesignModel
from .naming import name_similarity, package_depth, tokens

log = logging.getLogger(__name__)

W_NAME, W_STRUCT = 0.85, 0.70
MAX_CANDIDATE_DEPTH = 3
# Package/module lồng trong (hoặc chứa) package đã ánh xạ cho component KHÁC chỉ được nhận khi điểm tên
# đủ cao (vd "REST API" -> controllers.rest); chỉ trùng cấu trúc là chưa đủ.
NESTED_MIN_NAME = 0.5


@dataclass
class MappingResult:
    design_id: str
    packages: list[str] = field(default_factory=list)
    confidence: float = 0.0
    status: str = "unmapped"  # confirmed | uncertain | unmapped | not_applicable
    method: str = ""
    name_score: float = 0.0
    structure_score: Optional[float] = None
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    reason: str = ""


def combine(name: float, struct: Optional[float]) -> float:
    s = 0.0 if struct is None else struct
    return round(1 - (1 - W_NAME * name) * (1 - W_STRUCT * s), 4)


def _under(module: str, package: str) -> bool:
    return module == package or module.startswith(package + ".")


def _related(a: str, b: str) -> bool:
    return _under(a, b) or _under(b, a)


class GraphMatcher:
    def __init__(self, design: DesignModel, code: CodeModel, *, accept_threshold: float = 0.75,
                 candidate_threshold: float = 0.40, margin: float = 0.08, rounds: int = 2,
                 deep: bool = False, rejected: Optional[set[tuple[str, str]]] = None) -> None:
        self.design = design
        self.code = code
        self.accept = accept_threshold
        self.cand_thr = candidate_threshold
        self.margin = margin
        self.rounds = max(1, rounds)
        self.deep = deep
        self.rejected = rejected or set()  # cặp (design_id, package) người dùng đã bác bỏ
        self.candidates = self._candidate_packages()
        self._roots = self._root_packages()
        self.pkg_pairs = self._package_pairs()

    # -------------------------------------------------------------- chuẩn bị
    def _candidate_packages(self) -> list[str]:
        cands = {p for p in self.code.packages if package_depth(p) <= MAX_CANDIDATE_DEPTH}
        # module đơn lẻ (1 file) cũng có thể là một component, vd coordinator_agent/schema.py
        cands |= {m for m in self.code.modules if package_depth(m) <= MAX_CANDIDATE_DEPTH}
        return sorted(cands)

    def _root_packages(self) -> set[str]:
        """Package gốc chứa TOÀN BỘ module (vd 'coordinator_agent'): ánh xạ vào đó sẽ 'nuốt' mọi module
        chưa ánh xạ và che mất code rác, nên bị giảm điểm tên."""
        tops = {m.split(".")[0] for m in self.code.modules}
        return tops if len(tops) == 1 and len(self.code.modules) > 1 else set()

    def _package_pairs(self) -> set[tuple[str, str]]:
        """Tập (P, Q): có ít nhất một import từ module dưới P tới module dưới Q."""
        def ancestors(m: str) -> list[str]:
            parts = m.split(".")
            return [".".join(parts[:i]) for i in range(1, min(len(parts), MAX_CANDIDATE_DEPTH) + 1)]

        pairs: set[tuple[str, str]] = set()
        for d in self.code.dependencies:
            if d.resolution != "internal":
                continue
            for a in ancestors(d.source_module):
                for b in ancestors(d.target):
                    if not _related(a, b):
                        pairs.add((a, b))
        return pairs

    # -------------------------------------------------------------- điểm
    def _name_score(self, did: str, pkg: str) -> tuple[float, str]:
        comp = self.design.components[did]
        score, method = name_similarity([comp.name, comp.id], pkg)
        if self.deep:
            # Bằng chứng nội dung: token chính của D xuất hiện trong tên module bên dưới P
            d_toks = {t for t in tokens(comp.name) if len(t) > 2}
            if d_toks:
                mods = [m for m in self.code.modules if _under(m, pkg)]
                mod_toks = {t for m in mods for t in tokens(m.replace(".", " "))}
                content = len(d_toks & mod_toks) / len(d_toks)
                if 0.6 * content > score:
                    score, method = round(0.6 * content, 4), "content"
        if score >= 0.5 and pkg in self.code.packages:
            # Package cha đã có bằng chứng tên (vd "di" ~ acronym) và chứa file con trùng tên component
            # (di/dependency_injection.py) -> file con củng cố cho package cha, tránh ánh xạ vào 1 file lẻ.
            prefix = pkg + "."
            for child in self.code.modules:
                if child.startswith(prefix) and "." not in child[len(prefix):]:
                    cs, _ = name_similarity([comp.name, comp.id], child)
                    if cs > score:  # package cha nông hơn nên thắng file con khi điểm bằng nhau
                        score, method = cs, f"{method}+child"
        penalty = 1 - 0.03 * (package_depth(pkg) - 1)
        if pkg in self._roots:
            penalty *= 0.7
        return round(score * penalty, 4), method

    def _struct_score(self, did: str, pkg: str, anchors: dict[str, tuple[list[str], float]]) -> Optional[float]:
        """struct = Σ trọng_số(láng giềng khớp chiều) / số láng giềng đã neo.

        Neo chắc chắn có trọng số 1.0; neo "mềm" (ánh xạ uncertain ở vòng trước) có trọng số = confidence
        của nó, nên bằng chứng đi qua neo mềm chỉ được tính một phần.
        """
        g = self.design.graph
        relevant = 0
        hits = 0.0
        for other in g.successors(did):
            if other in anchors and other != did:
                pkgs, w = anchors[other]
                relevant += 1
                if any((pkg, q) in self.pkg_pairs for q in pkgs if not _related(pkg, q)):
                    hits += w
        for other in g.predecessors(did):
            if other in anchors and other != did:
                pkgs, w = anchors[other]
                relevant += 1
                if any((q, pkg) in self.pkg_pairs for q in pkgs if not _related(pkg, q)):
                    hits += w
        if relevant == 0:
            return None
        return round(hits / relevant, 4)

    # -------------------------------------------------------------- chạy
    def run(self, explicit: dict[str, list[str]], reused: Optional[dict[str, tuple[list[str], float, str]]] = None
            ) -> dict[str, MappingResult]:
        """``explicit``: design_id -> packages (neo chắc chắn).
        ``reused``: design_id -> (packages, confidence, method) lấy từ Knowledge (không tính lại)."""
        reused = reused or {}
        results: dict[str, MappingResult] = {}
        anchors: dict[str, tuple[list[str], float]] = {}  # design_id -> (packages, trọng số)

        for did, comp in self.design.components.items():
            if not comp.expected_in_code:
                results[did] = MappingResult(did, status="not_applicable", method="n/a",
                                             reason=f"Loại '{comp.type}' mô tả bối cảnh, không kỳ vọng có trong mã nguồn.")
        for did, pkgs in explicit.items():
            if did not in self.design.components:
                continue
            valid = [p for p in pkgs if p in self.code.packages or p in self.code.modules]
            if valid:
                results[did] = MappingResult(did, valid, 1.0, "confirmed", "explicit", 1.0, None,
                                             reason="Ánh xạ khai báo bởi người dùng/AEA.")
                anchors[did] = (valid, 1.0)
            else:
                results[did] = MappingResult(did, [], 0.0, "unmapped", "explicit",
                                             reason=f"Ánh xạ khai báo {pkgs} không tồn tại trong mã nguồn.")
        for did, (pkgs, conf, method) in reused.items():
            if did in results or did not in self.design.components:
                continue
            valid = [p for p in pkgs if p in self.code.packages or p in self.code.modules]
            if valid:
                status = "confirmed" if conf >= self.accept else "uncertain"
                results[did] = MappingResult(did, valid, conf, status, method,
                                             reason="Tái sử dụng ánh xạ từ Knowledge (component không thay đổi).")
                anchors[did] = (valid, 1.0 if status == "confirmed" else conf)

        fixed = set(results)
        taken = {p for r in results.values() for p in r.packages}
        open_ids = [d for d in self.design.components if d not in fixed]

        round_results: dict[str, MappingResult] = {}
        per_design: dict[str, list[tuple[str, float]]] = {}
        for rnd in range(self.rounds):
            scored: list[tuple[float, str, str, float, Optional[float], str]] = []
            per_design = {d: [] for d in open_ids}
            for did in open_ids:
                for pkg in self.candidates:
                    if pkg in taken or (did, pkg) in self.rejected:
                        continue
                    ns, nm = self._name_score(did, pkg)
                    if ns < NESTED_MIN_NAME and any(_related(pkg, t) for t in taken):
                        continue  # nằm trong/chứa package của component khác -> cần bằng chứng tên
                    ss = self._struct_score(did, pkg, anchors)
                    if ns == 0 and not ss:
                        continue
                    conf = combine(ns, ss)
                    if conf < self.cand_thr * 0.5:
                        continue
                    scored.append((conf, did, pkg, ns, ss, nm))
                    per_design[did].append((pkg, conf))
            scored.sort(key=lambda x: (-x[0], package_depth(x[2]), x[2]))

            assigned: dict[str, MappingResult] = {}
            used: set[str] = set()
            for conf, did, pkg, ns, ss, nm in scored:
                if did in assigned or pkg in used:
                    continue
                if ns < NESTED_MIN_NAME and any(_related(pkg, u) for u in used):
                    continue  # không cho component khác "giành" file con chỉ nhờ cấu trúc
                alts = sorted((c for c in per_design[did] if c[0] != pkg), key=lambda c: -c[1])
                rival = next((c for c in alts if not _related(c[0], pkg)), None)
                ambiguous = rival is not None and conf - rival[1] < self.margin
                method = f"{nm}+structure" if ss is not None else f"{nm}_only"
                if ss is not None and ns < 0.2:  # điểm tên không đáng kể
                    method = "structure_only"
                if conf >= self.accept and not ambiguous:
                    status, reason = "confirmed", "Đủ bằng chứng tên + cấu trúc." if ss else "Tên trùng khớp sau chuẩn hóa."
                elif conf >= self.cand_thr:
                    status = "uncertain"
                    if ambiguous and rival is not None:
                        reason = f"Mơ hồ: '{rival[0]}' có điểm gần bằng ({rival[1]:.2f})."
                    elif ss is None:
                        reason = "Chỉ dựa trên tên gần giống, chưa có bằng chứng cấu trúc."
                    else:
                        reason = "Bằng chứng tên/cấu trúc chưa đủ mạnh."
                else:
                    continue
                assigned[did] = MappingResult(did, [pkg], conf, status, method, ns, ss,
                                              [(p, c) for p, c in alts[:3]], reason)
                used.add(pkg)

            new_anchors = {d: (r.packages, 1.0 if r.status == "confirmed" else r.confidence)
                           for d, r in assigned.items()}
            stable = round_results and {d: (r.packages, r.status) for d, r in assigned.items()} == \
                {d: (r.packages, r.status) for d, r in round_results.items()}
            round_results = assigned
            log.debug("matching round %d: %d assigned, %d confirmed", rnd, len(assigned),
                      sum(1 for r in assigned.values() if r.status == "confirmed"))
            if stable:
                break
            anchors = {**{d: p for d, p in anchors.items() if d in fixed}, **new_anchors}

        results.update(round_results)
        for did in open_ids:
            if did not in results:
                alts = sorted(per_design.get(did, []), key=lambda c: -c[1])[:3]
                results[did] = MappingResult(did, status="unmapped", method="none", alternatives=alts,
                                             reason="Không có package nào đủ điểm tên hoặc cấu trúc.")
        return results


def code_group(name: str, code: CodeModel) -> str:
    """Nhóm module chưa ánh xạ thành một node 'code:<nhóm>'.

    Thường là package cấp cao nhất; nhưng nếu cả dự án nằm trong MỘT package gốc (vd coordinator_agent/),
    nhóm theo cấp thứ hai để báo đúng tên (coordinator_agent.intermediate_store) thay vì cả package gốc.
    """
    parts = name.split(".")
    tops = {m.split(".")[0] for m in code.modules}
    if len(tops) == 1 and len(parts) >= 2 and parts[0] in tops:
        return "code:" + ".".join(parts[:2])
    return f"code:{parts[0]}"


def assign_modules(code: CodeModel, mappings: dict[str, MappingResult]) -> dict[str, str]:
    """module -> node id của đồ thị thực tế (design_id nếu ánh xạ được, ngược lại 'code:<top>')."""
    pkg_to_design: list[tuple[str, str]] = []
    for did, r in mappings.items():
        if r.status in ("confirmed", "uncertain"):
            pkg_to_design.extend((p, did) for p in r.packages)
    pkg_to_design.sort(key=lambda x: -len(x[0]))
    out: dict[str, str] = {}
    for m in code.modules:
        owner = next((did for p, did in pkg_to_design if _under(m, p)), None)
        out[m] = owner or code_group(m, code)
    return out


def owner_of(target: str, module_owner: dict[str, str], mappings: dict[str, MappingResult],
             code: Optional[CodeModel] = None) -> str:
    """Tìm node sở hữu một đích import (module, package hoặc package "ma")."""
    if target in module_owner:
        return module_owner[target]
    best: Optional[tuple[int, str]] = None
    for did, r in mappings.items():
        if r.status not in ("confirmed", "uncertain"):
            continue
        for p in r.packages:
            if _under(target, p) and (best is None or len(p) > best[0]):
                best = (len(p), did)
    if best:
        return best[1]
    # package cha của nhiều module (vd "repositories") -> lấy chủ của module con đầu tiên
    children = sorted(m for m in module_owner if _under(m, target))
    if children:
        owners = {module_owner[c] for c in children}
        if len(owners) == 1:
            return owners.pop()
    return code_group(target, code) if code is not None else f"code:{target.split('.')[0]}"
