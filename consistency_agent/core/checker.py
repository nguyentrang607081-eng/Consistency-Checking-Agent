"""Bước EXECUTE: dựng kiến trúc thực tế, so sánh với thiết kế (Reflexion Model), sinh finding.

  ánh xạ (graph_matching) -> nâng phụ thuộc module lên mức component -> đồ thị thực tế
  -> với mỗi cạnh thực tế: Rule Engine  (convergence / divergence)
  -> với mỗi cạnh thiết kế không có trong code: absence
  -> component thiếu / thừa / ánh xạ chưa chắc / chu trình
  -> drift & similarity
"""
from __future__ import annotations

import hashlib
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

import networkx as nx

from ..schemas.inputs import RuleSet
from ..schemas.internal import CodeModel, DesignModel
from ..schemas.outputs import (ComponentMapping, DependencyCheck, DriftMetrics, EvidenceItem, Finding,
                               GraphEdgeView, GraphNodeView, MappingCandidate, VisualizationData)
from . import confidence as cf
from . import drift as drift_mod
from .graph_matching import MappingResult, assign_modules, code_group, owner_of
from .rule_engine import RuleEngine, Verdict

log = logging.getLogger(__name__)

MAX_EVIDENCE = 5
SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
DEP_TYPES = {"forbidden_dependency", "layer_violation", "reverse_dependency", "unexpected_dependency",
             "missing_dependency"}

ACTIONS = {
    "forbidden_dependency": "Loại bỏ phụ thuộc trực tiếp {s} -> {t}; đi qua component trung gian được phép "
                            "(vd interface/use case) hoặc đảo phụ thuộc bằng abstraction.",
    "layer_violation": "Đảo chiều phụ thuộc: định nghĩa interface ở layer trong ({s}) và để layer ngoài ({t}) "
                       "hiện thực nó (Dependency Inversion).",
    "reverse_dependency": "Kiểm tra chiều phụ thuộc {s} -> {t}: thiết kế chỉ cho phép {t} -> {s}. "
                          "Tách phần dùng chung ra module riêng hoặc dùng callback/event.",
    "cyclic_dependency": "Phá chu trình {cycle}: tách interface dùng chung hoặc chuyển phụ thuộc sang event.",
    "unexpected_dependency": "Xác nhận phụ thuộc {s} -> {t}: nếu có chủ đích, cập nhật tài liệu kiến trúc / "
                             "thêm luật allow; nếu không, gỡ bỏ.",
    "missing_dependency": "Thiết kế yêu cầu {s} -> {t} nhưng không thấy import tương ứng. Kiểm tra lời gọi "
                          "động/HTTP; nếu thiết kế đã lỗi thời, cập nhật tài liệu.",
    "missing_component": "Hiện thực component '{s}' hoặc khai báo ánh xạ (component_mapping) nếu nó nằm ở "
                         "package khác tên; nếu đã bỏ, cập nhật tài liệu kiến trúc.",
    "unexpected_component": "Bổ sung '{s}' vào tài liệu kiến trúc (nếu là component hợp lệ) hoặc gộp vào "
                            "component phù hợp; nếu là import tới package không tồn tại, sửa import.",
    "uncertain_mapping": "Xác nhận ánh xạ '{s}' -> package '{t}' (lệnh `feedback confirm-mapping`) hoặc "
                         "bác bỏ (`feedback reject-mapping`).",
}


@dataclass
class EdgeInfo:
    evidence: list[EvidenceItem] = field(default_factory=list)
    count: int = 0
    phantom: bool = False


@dataclass
class CheckOutput:
    findings: list[Finding]
    dependencies: list[DependencyCheck]
    mappings: list[ComponentMapping]
    drift: DriftMetrics
    visualization: VisualizationData
    actual_edges: set[tuple[str, str]]
    package_signatures: dict[str, str]


def fingerprint(vtype: str, source: str, target: str, rule_id: str) -> str:
    return hashlib.sha1(f"{vtype}|{source}|{target}|{rule_id}".encode()).hexdigest()[:16]


def package_signatures(code: CodeModel) -> dict[str, str]:
    """Chữ ký từng package cấp cao: đổi khi module hoặc phụ thuộc nội bộ của package đổi."""
    acc: dict[str, list[str]] = defaultdict(list)
    for m in code.modules:
        acc[m.split(".")[0]].append(f"M:{m}")
    for d in code.dependencies:
        if d.resolution != "external":
            acc[d.source_module.split(".")[0]].append(f"D:{d.source_module}>{d.target}")
        else:
            acc[d.source_module.split(".")[0]].append(f"X:{d.source_module}>{d.target}")
    return {k: hashlib.sha1("\n".join(sorted(v)).encode()).hexdigest()[:16] for k, v in acc.items()}


class ConsistencyChecker:
    def __init__(self, design: DesignModel, code: CodeModel, rules: RuleSet, mappings: dict[str, MappingResult],
                 threshold: float) -> None:
        self.design = design
        self.code = code
        self.rules = rules
        self.mappings = mappings
        self.threshold = threshold
        self.engine = RuleEngine(rules, design)
        self.code_factor = cf.SCA_STATUS_FACTOR.get(code.status, 0.8)
        ignored = set(rules.ignore_code_packages)
        self.ignored_nodes = {f"code:{p}" for p in ignored}

    # ------------------------------------------------------------ helpers
    def _label(self, node: str) -> str:
        if node in self.design.components:
            return node
        return node[5:] if node.startswith("code:") else node

    def _map_conf(self, node: str) -> float:
        r = self.mappings.get(node)
        if r is None:
            return cf.CODE_ONLY_NODE_MAPPING
        return r.confidence if r.status in ("confirmed", "uncertain") else 0.0

    def _completeness(self, *nodes: str) -> float:
        confs = [self.design.components[n].confidence for n in nodes if n in self.design.components]
        design_factor = sum(confs) / len(confs) if confs else 1.0
        return self.code_factor * design_factor

    def _finding(self, vtype: str, severity: str, conf_parts: tuple[float, float, float, float], rule_id: str,
                 description: str, source: str = "", target: str = "", expected: str = "", actual: str = "",
                 evidence: Optional[list[EvidenceItem]] = None, detected_by: str = "rule_engine",
                 undetermined: bool = False, force_review: bool = False, action_ctx: Optional[dict] = None) -> Finding:
        conf, breakdown = cf.score(*conf_parts)
        status = cf.status_for(conf, self.threshold, undetermined)
        if force_review and status == "confirmed":
            status = "needs_review"
        ev = (evidence or [])[:MAX_EVIDENCE]
        ctx = {"s": self._label(source), "t": self._label(target), "cycle": ""}
        ctx.update(action_ctx or {})
        return Finding(
            finding_id="", fingerprint=fingerprint(vtype, source, target, rule_id), violation_type=vtype,
            severity=severity, confidence_score=conf, confidence_breakdown=breakdown, status=status,
            detected_by=detected_by, rule_id=rule_id, description=description,
            source_component=self._label(source), target_component=self._label(target),
            expected_dependency=expected, actual_dependency=actual,
            file_path=ev[0].file_path if ev else "", line_number=ev[0].line_start if ev else 0,
            evidence=ev, recommended_action=ACTIONS.get(vtype, "").format(**ctx),
        )

    # ------------------------------------------------------------ main
    def run(self, focus: Optional[set[str]] = None, decisions: Optional[dict[str, dict]] = None,
            previous_findings: Optional[list[dict]] = None, previous_drift: Optional[float] = None,
            previous_edges: Optional[set[tuple[str, str]]] = None) -> CheckOutput:
        decisions = decisions or {}
        module_owner = assign_modules(self.code, self.mappings)
        design_nodes = {cid for cid, c in self.design.components.items() if c.expected_in_code}
        mapped = {d for d, r in self.mappings.items() if r.status in ("confirmed", "uncertain")}

        # ---- 1. đồ thị thực tế mức component
        edges: dict[tuple[str, str], EdgeInfo] = defaultdict(EdgeInfo)
        ext: dict[tuple[str, str], EdgeInfo] = defaultdict(EdgeInfo)
        ext_full: dict[tuple[str, str], set[str]] = defaultdict(set)
        actual_nodes: set[str] = set(mapped)
        node_modules: dict[str, list[str]] = defaultdict(list)
        for m, owner in module_owner.items():
            if owner in self.ignored_nodes:
                continue
            actual_nodes.add(owner)
            node_modules[owner].append(m)
        phantom_nodes: set[str] = set()
        for d in self.code.dependencies:
            s = module_owner.get(d.source_module)
            if s is None or s in self.ignored_nodes:
                continue
            dep_ev = EvidenceItem(file_path=d.evidence.file_path, line_start=d.evidence.line_start,
                                  line_end=d.evidence.line_end,
                                  statement=f"{d.dependency_type} {d.evidence.raw_import} -> {d.target} ({d.resolution})")
            if d.resolution == "external":
                info = ext[(s, d.target.split(".")[0])]  # gộp theo thư viện cấp cao (sqlalchemy.orm -> sqlalchemy)
                info.count += 1
                info.evidence.append(dep_ev)
                ext_full[(s, d.target.split(".")[0])].add(d.target)
                continue
            if d.resolution == "unresolved_internal":
                t = code_group(d.target, self.code)
                if t not in actual_nodes or t in phantom_nodes:
                    phantom_nodes.add(t)
            else:
                t = owner_of(d.target, module_owner, self.mappings, self.code)
            if t == s or t in self.ignored_nodes:
                continue
            actual_nodes.add(t)
            info = edges[(s, t)]
            info.count += 1
            info.phantom = info.phantom or d.resolution == "unresolved_internal"
            info.evidence.append(dep_ev)
        phantom_nodes -= set(node_modules)
        actual_edges = set(edges)
        design_edges = {(a, b) for a, b in self.design.graph.edges if a in design_nodes and b in design_nodes}

        findings: list[Finding] = []
        deps: list[DependencyCheck] = []

        def in_focus(*nodes: str) -> bool:
            return focus is None or any(n in focus for n in nodes)

        # ---- 2. cạnh thực tế: Rule Engine
        for (s, t), info in sorted(edges.items()):
            v = self.engine.evaluate(s, t)
            reflexion = "convergence" if (s, t) in design_edges else "divergence"
            deps.append(DependencyCheck(source=self._label(s), target=self._label(t), reflexion=reflexion,
                                        verdict=v.verdict, rule_id=v.rule_id, occurrences=info.count,
                                        evidence=info.evidence[:MAX_EVIDENCE]))
            if v.violation_type is None or not in_focus(s, t):
                continue
            code_only_endpoint = s.startswith("code:") or t.startswith("code:")
            if v.verdict == "undetermined" and v.rule_id.startswith("DEFAULT") and code_only_endpoint:
                continue  # đã được báo trong finding unexpected_component
            evidence_f = cf.EVIDENCE_PHANTOM if info.phantom else cf.EVIDENCE_IMPORT
            parts = (min(self._map_conf(s), self._map_conf(t)), v.clarity, evidence_f, self._completeness(s, t))
            ef = self._finding(
                v.violation_type, v.severity or "medium", parts, v.rule_id,
                f"{v.description} ({info.count} lần import)", s, t, v.expected,
                f"{self._label(s)} -> {self._label(t)}", info.evidence,
                detected_by="rule_engine" if v.verdict == "forbidden" else "graph_matching",
                undetermined=v.verdict == "undetermined")
            if code_only_endpoint:
                ef.scenario_detail = "to_junk"  # phụ thuộc nối với package ngoài kiến trúc
            findings.append(ef)

        # ---- 3. thư viện ngoài (chỉ luật tường minh external:...)
        for (s, lib), info in sorted(ext.items()):
            xv: Optional[Verdict] = self.engine.evaluate_external(s, lib)
            for full in sorted(ext_full[(s, lib)]):  # luật có thể nhắm module con (external:sqlalchemy.orm)
                if xv is not None:
                    break
                xv = self.engine.evaluate_external(s, full)
            if xv is None or xv.violation_type is None or not in_focus(s):
                continue
            parts = (self._map_conf(s), xv.clarity, cf.EVIDENCE_IMPORT, self._completeness(s))
            findings.append(self._finding(
                xv.violation_type, xv.severity or "high", parts, xv.rule_id, xv.description, s, f"external:{lib}",
                xv.expected, f"{self._label(s)} -> {lib}", info.evidence,
                undetermined=xv.verdict == "undetermined"))

        # ---- 4. absence: cạnh thiết kế không có trong code
        for s, t in sorted(design_edges - actual_edges):
            both = s in mapped and t in mapped
            deps.append(DependencyCheck(source=s, target=t, reflexion="absence", verdict="missing",
                                        rule_id=f"DESIGN:{s}->{t}", occurrences=0))
            if not both or not in_focus(s, t):
                continue  # một đầu chưa ánh xạ -> đã báo ở missing_component
            parts = (min(self._map_conf(s), self._map_conf(t)), 0.85, cf.EVIDENCE_ABSENCE, self._completeness(s, t))
            findings.append(self._finding(
                "missing_dependency", "medium", parts, f"DESIGN:{s}->{t}",
                f"Thiết kế có {s} -> {t} nhưng không tìm thấy import tương ứng trong mã nguồn.",
                s, t, f"{s} -> {t}", "(không có)", detected_by="graph_matching"))
        for s, t, rule in self.engine.required_pairs(sorted(actual_nodes)):
            if (s, t) in actual_edges or not in_focus(s, t):
                continue
            parts = (min(self._map_conf(s), self._map_conf(t)), 1.0, cf.EVIDENCE_ABSENCE, self._completeness(s, t))
            findings.append(self._finding(
                "missing_dependency", rule.severity or "high", parts, rule.rule_id,
                rule.description or f"Luật {rule.rule_id} yêu cầu {s} -> {t} nhưng không có trong code.",
                s, t, f"{self._label(s)} -> {self._label(t)} (bắt buộc)", "(không có)"))

        # ---- 5. component thiếu / ánh xạ chưa chắc
        for did in sorted(design_nodes):
            r = self.mappings.get(did)
            if r is None:
                continue
            degree = self.design.graph.degree(did)
            if r.status == "unmapped":
                best_alt = max((c for _, c in r.alternatives), default=0.0)
                parts = (max(0.3, 1 - best_alt), 0.9, cf.EVIDENCE_ABSENCE, self._completeness(did))
                hint = f" Ứng viên gần nhất: {r.alternatives[0][0]} ({best_alt:.2f})." if r.alternatives else ""
                findings.append(self._finding(
                    "missing_component", "high" if degree else "medium", parts, f"DESIGN:{did}",
                    f"Component thiết kế '{self.design.components[did].name}' ({did}) không tìm thấy trong mã nguồn."
                    + hint, did, "", f"{did} tồn tại trong code", "(không tìm thấy)", detected_by="graph_matching"))
            elif r.status == "uncertain":
                parts = (r.confidence, 1.0, 1.0, self._completeness(did))
                findings.append(self._finding(
                    "uncertain_mapping", "low", parts, f"MAP:{did}",
                    f"Ánh xạ '{self.design.components[did].name}' -> package '{r.packages[0]}' chưa chắc chắn "
                    f"(confidence {r.confidence:.2f}): {r.reason}", did, f"code:{r.packages[0]}",
                    f"{did} <-> ?", f"{did} <-> {r.packages[0]}", detected_by="similarity", force_review=True))

        # ---- 6. component thừa (có trong code, không có trong thiết kế)
        for node in sorted(n for n in actual_nodes if n.startswith("code:") and n not in self.ignored_nodes):
            is_phantom = node in phantom_nodes
            related = [(a, b) for (a, b) in actual_edges if node in (a, b)]
            if (not is_phantom and not related and node_modules[node]
                    and all(self.code.modules[m].endswith("__init__.py") for m in node_modules[node])):
                continue  # chỉ là __init__.py rỗng của package (vd package gốc) -> không phải component
            comp_ev: list[EvidenceItem] = []
            for e in related:
                comp_ev.extend(edges[e].evidence[:2])
            if not is_phantom:
                files_first = sorted(node_modules[node], key=lambda x: (self.code.modules[x].endswith("__init__.py"), x))
                comp_ev = [EvidenceItem(file_path=self.code.modules[m], statement=f"module {m}")
                           for m in files_first[:3]] + comp_ev
            touches_design = any(a in mapped or b in mapped for a, b in related)
            used_by_design = any(b == node and a in mapped for a, b in related)
            junk_kind = "phantom" if is_phantom else ("undocumented" if used_by_design else "orphan")
            # package "ma": import tới module không tồn tại -> lỗi chắc chắn (ImportError khi chạy)
            sev = "high" if is_phantom else ("medium" if touches_design else "low")
            name = node[5:]
            desc = (f"Package '{name}' được import nhưng KHÔNG tồn tại trong mã nguồn (package ma), "
                    f"cũng không có trong kiến trúc." if is_phantom else
                    f"Package '{name}' ({len(node_modules[node])} module) có trong mã nguồn nhưng không được mô tả "
                    f"trong kiến trúc thiết kế" + (
                        "; không component thiết kế nào dùng tới -> ứng viên code chết." if junk_kind == "orphan"
                        else "; đang được component thiết kế sử dụng -> cần bổ sung vào tài liệu."))
            parts = (cf.CODE_ONLY_NODE_MAPPING, 0.9 if is_phantom else 0.8, cf.EVIDENCE_IMPORT, self.code_factor)
            if not in_focus(node, *[x for e in related for x in e]):
                continue
            uf = self._finding(
                "unexpected_component", sev, parts, "DESIGN:component-set", desc, node, "",
                "(không có trong thiết kế)", f"{name} tồn tại trong code", comp_ev, detected_by="graph_matching")
            uf.scenario_detail = junk_kind
            findings.append(uf)

        # ---- 7. chu trình phụ thuộc (dấu hiệu erosion)
        g = nx.DiGraph()
        g.add_edges_from(sorted(actual_edges))  # thứ tự cố định -> kết quả lặp lại được
        seen: set[frozenset] = set()
        for raw_cyc in nx.simple_cycles(g, length_bound=6):
            start = raw_cyc.index(min(raw_cyc))  # xoay về node nhỏ nhất -> fingerprint ổn định giữa các lần chạy
            cyc = raw_cyc[start:] + raw_cyc[:start]
            key = frozenset(cyc)
            if key in seen or len(seen) >= 50:
                continue
            seen.add(key)
            pairs = list(zip(cyc, cyc[1:] + cyc[:1]))
            if all(p in design_edges for p in pairs) or not in_focus(*cyc):
                continue
            if len(cyc) == 2 and any(p in design_edges for p in pairs):
                continue  # chu trình 2 node có một chiều đúng thiết kế -> đã báo là reverse_dependency
            path = " -> ".join(self._label(n) for n in [*cyc, cyc[0]])
            cyc_ev = [edges[p].evidence[0] for p in pairs if edges[p].evidence]
            parts = (min(self._map_conf(n) for n in cyc), 0.9, cf.EVIDENCE_IMPORT, self._completeness(*cyc))
            findings.append(self._finding(
                "cyclic_dependency", "high" if len(cyc) == 2 else "medium", parts, "STRUCT:acyclic",
                f"Chu trình phụ thuộc giữa các component: {path}.", cyc[0], cyc[1], "(không có chu trình)", path,
                cyc_ev, detected_by="graph_matching", action_ctx={"cycle": path}))

        # ---- 8. quyết định của người dùng (Knowledge) + carry-over (incremental)
        for f in findings:
            dec = decisions.get(f.fingerprint)
            if dec:
                f.status = "accepted"
                f.description += f" [Người dùng đã đánh dấu: {dec['decision']}" + \
                    (f" - {dec['note']}" if dec.get("note") else "") + "]"
        if focus is not None and previous_findings:
            current = {f.fingerprint for f in findings}
            focus_labels = {self._label(n) for n in focus}
            for pf in previous_findings:
                if pf.get("violation_type") not in DEP_TYPES | {"cyclic_dependency"}:
                    continue
                if pf.get("source_component") in focus_labels or pf.get("target_component") in focus_labels:
                    continue
                if pf.get("fingerprint") in current:
                    continue
                carried = Finding.model_validate({**pf, "carried_over": True})
                dec = decisions.get(carried.fingerprint)
                if dec:
                    carried.status = "accepted"
                findings.append(carried)

        findings.sort(key=lambda f: (SEV_ORDER[f.severity], -f.confidence_score, f.violation_type, f.source_component))
        for i, f in enumerate(findings, 1):
            f.finding_id = f"CCA-{i:04d}"

        drift = drift_mod.compute(design_nodes, {n for n in actual_nodes if n not in self.ignored_nodes},
                                  design_edges, actual_edges, findings, previous_drift, previous_edges)
        return CheckOutput(findings, deps, self._mapping_views(node_modules), drift,
                           self._viz(design_nodes, actual_nodes, phantom_nodes, design_edges, edges, deps),
                           actual_edges, package_signatures(self.code))

    # ------------------------------------------------------------ views
    def _mapping_views(self, node_modules: dict[str, list[str]]) -> list[ComponentMapping]:
        out = []
        for did, comp in self.design.components.items():
            r = self.mappings.get(did) or MappingResult(did)
            out.append(ComponentMapping(
                design_id=did, design_name=comp.name, design_type=comp.type, code_packages=r.packages,
                module_count=len(node_modules.get(did, [])), confidence=r.confidence, status=r.status,  # type: ignore
                method=r.method, name_score=r.name_score, structure_score=r.structure_score,
                alternatives=[MappingCandidate(code_package=p, score=s) for p, s in r.alternatives],
                reason=r.reason))
        return out

    def _viz(self, design_nodes: set[str], actual_nodes: set[str], phantom: set[str],
             design_edges: set[tuple[str, str]], edges: dict, deps: list[DependencyCheck]) -> VisualizationData:
        nodes = []
        for did, comp in self.design.components.items():
            r = self.mappings.get(did)
            status = {"confirmed": "matched", "uncertain": "uncertain", "unmapped": "missing",
                      "not_applicable": "context"}.get(r.status if r else "unmapped", "missing")
            nodes.append(GraphNodeView(id=did, label=comp.name, kind="design", status=status,
                                       layer=self.engine.layer(did)))
        for n in sorted(actual_nodes - set(self.design.components)):
            if n in self.ignored_nodes:
                continue
            nodes.append(GraphNodeView(id=n, label=n[5:], kind="phantom" if n in phantom else "code",
                                       status="unexpected", layer=self.engine.layer(n)))
        verdict = {(d.source, d.target): d.verdict for d in deps}
        out_edges = []
        for (a, b), info in sorted(edges.items()):
            out_edges.append(GraphEdgeView(source=a, target=b,
                                           reflexion="convergence" if (a, b) in design_edges else "divergence",
                                           verdict=verdict.get((self._label(a), self._label(b)), ""),
                                           weight=info.count))
        for a, b in sorted(design_edges - set(edges)):
            out_edges.append(GraphEdgeView(source=a, target=b, reflexion="absence", verdict="missing"))
        return VisualizationData(nodes=nodes, edges=out_edges)
