"""Giao diện dòng lệnh của Consistency Checking Agent.

    python -m consistency_agent check --design A.json --code B.json --rules R.json -o outputs/report.json
    python -m consistency_agent demo
    python -m consistency_agent scenarios                     # demo 4 kịch bản (khớp / sai / thiếu / rác)
    python -m consistency_agent scenarios --llm-provider groq # rà soát thêm bằng LLM (cần GROQ_API_KEY)
    python -m consistency_agent extract-code <thu_muc_ma_nguon> -o code.json
    python -m consistency_agent feedback accept --project P --fingerprint FP --note "..."
    python -m consistency_agent feedback confirm-mapping --project P --design-id entities --packages models
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

from .agent import ConsistencyCheckingAgent
from .config import AgentConfig
from .integration.coordinator import to_coordinator_input
from .integration.recommendation import to_recommendation_request
from .schemas.outputs import ConsistencyReport

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"


def _load(path: Optional[str]) -> Optional[dict]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Không tìm thấy file: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8-sig"))  # -sig: chấp nhận file có BOM (Notepad Windows)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"File JSON không hợp lệ: {p} (dòng {exc.lineno}, cột {exc.colno}: {exc.msg})")


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def print_summary(r: ConsistencyReport, verbose: bool = False) -> None:
    print(f"\n=== Consistency Checking Agent | {r.project_id} / {r.scan_id} | {r.status} ===")
    s = r.strategy
    print(f"Chiến lược: scope={s.scope} focus={s.focus_components or '-'} deep={s.deep_matching} "
          f"threshold={s.confidence_threshold} (nguồn: {s.source})")
    for reason in s.reasons:
        print(f"  - {reason}")
    print("\nÁnh xạ component (thiết kế -> mã nguồn):")
    for m in r.mappings:
        if m.status == "not_applicable":
            continue
        print(f"  {m.design_id:24} -> {', '.join(m.code_packages) or '(không có)':24} "
              f"{m.status:10} conf={m.confidence:.2f} [{m.method}]")
    d = r.drift
    print(f"\nDrift: score={d.drift_score:.3f} ({d.drift_level}) | similarity={d.structural_similarity:.3f} | "
          f"conformance={d.dependency_conformance:.2f} | realization={d.dependency_realization:.2f} | "
          f"violation_index={d.violation_index:.3f}" + (f" | trend={d.drift_trend}" if d.drift_trend else ""))
    print(f"Reflexion: {d.convergences} convergence, {d.divergences} divergence, {d.absences} absence")
    print(f"\nFindings: {r.statistics.total_findings} (vi phạm đang mở: {r.statistics.open_violations}) "
          f"| theo trạng thái: {r.statistics.by_status}")
    for f in r.findings:
        loc = (f"{f.file_path}:{f.line_number}" if f.line_number else f.file_path) if f.file_path else ""
        tgt = f" -> {f.target_component}" if f.target_component else ""
        print(f"  {f.finding_id} [{f.severity:8}] {(f.scenario or '-'):9} {f.violation_type:22} "
              f"{f.source_component}{tgt} conf={f.confidence_score:.2f} {f.status}"
              f"{' (carried)' if f.carried_over else ''} {loc}")
    sr = r.scenario_report
    if sr:
        print(f"\nKỊCH BẢN: {sr.verdict.upper()} | chính: {sr.primary_scenario} | {sr.counts} | "
              f"tỷ lệ khớp {sr.match_ratio:.0%}")
        print(f"  Tóm tắt ({sr.summary_by}): {sr.summary}")
    rv = r.review
    if rv.mode != "off":
        print(f"Review: mode={rv.mode} provider={rv.provider} model={rv.model} reviewed={rv.reviewed} "
              f"llm_calls={rv.llm_calls} cache_hits={rv.cache_hits} fallbacks={rv.fallbacks} "
              f"disagreements={rv.disagreements} status_changes={rv.status_changes} "
              f"| RAG docs={rv.rag_documents} few-shot={rv.few_shot_pool}")
        for f in r.findings:
            if f.llm_review and (verbose or f.llm_review.verdict != "confirm"):
                lr = f.llm_review
                print(f"  {f.finding_id} reviewer={lr.verdict}/{lr.scenario} ({lr.confidence:.2f}): {lr.rationale[:160]}")
        if rv.errors:
            print("  Lỗi LLM: " + " | ".join(rv.errors[:3]))
    if r.warnings:
        print(f"\nCảnh báo ({len(r.warnings)}): " + " | ".join(r.warnings[:5]))
    if r.errors:
        print("LỖI: " + " | ".join(r.errors))
    print(f"Thời gian: {r.performance.total_ms:.1f} ms\n")


def _llm_config(a: argparse.Namespace, cfg: AgentConfig) -> AgentConfig:
    import os
    if getattr(a, "llm_provider", None):
        cfg.llm_provider = a.llm_provider
    if getattr(a, "llm_model", None):
        os.environ["CCA_LLM_MODEL"] = a.llm_model
    if getattr(a, "llm_base_url", None):
        os.environ["CCA_LLM_BASE_URL"] = a.llm_base_url
    if getattr(a, "llm_policy", None):
        cfg.llm_policy = a.llm_policy
    if getattr(a, "llm_max_calls", None) is not None:
        cfg.llm_max_calls = a.llm_max_calls
    return cfg


def _add_llm_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("RAG + few-shot + LLM")
    g.add_argument("--llm-provider", choices=["none", "local", "groq", "ollama", "openai", "openai_compat"],
                   help="Reviewer (mặc định: local - offline, không cần key)")
    g.add_argument("--llm-model", help="Tên model (vd llama-3.3-70b-versatile, qwen2.5:7b-instruct)")
    g.add_argument("--llm-base-url", help="URL /v1 cho openai_compat")
    g.add_argument("--llm-policy", choices=["advisory", "resolve"])
    g.add_argument("--llm-max-calls", type=int, help="Ngân sách số lần gọi LLM mỗi lần quét")


def cmd_check(a: argparse.Namespace) -> int:
    cfg = _llm_config(a, AgentConfig())
    if a.threshold is not None:
        cfg.confidence_threshold = a.threshold
    agent = ConsistencyCheckingAgent(cfg, knowledge=False if a.no_kb else (a.kb or None))
    ctx = {"project_id": a.project_id, "scan_id": a.scan_id, "commit_hash": a.commit or ""}
    report = agent.check(_load(a.design), _load(a.code), _load(a.rules), _load(a.strategy),
                         {k: v for k, v in ctx.items() if v})
    out = Path(a.output)
    _write(out, report.model_dump(mode="json"))
    if a.coordinator_output:
        _write(Path(a.coordinator_output), to_coordinator_input(report))
    if a.recommendation_output:
        _write(Path(a.recommendation_output), to_recommendation_request(report))
    if not a.quiet:
        print_summary(report, a.verbose)
    print(f"Đã ghi báo cáo JSON: {out}")
    return 0 if report.status != "FAILED" else 2


def cmd_demo(a: argparse.Namespace) -> int:
    out_dir = Path(a.output_dir)
    kb_path = out_dir / "demo_knowledge.sqlite"
    if kb_path.exists():
        kb_path.unlink()
    agent = ConsistencyCheckingAgent(knowledge=str(kb_path))
    ex = EXAMPLES / "py_clean_arch"
    rules = _load(str(ex / "rules.json"))
    design = _load(str(ex / "design_architecture.json"))
    print("Demo 1/3 - py-clean-arch bản gốc (dữ liệu test của nhóm SCA)")
    r1 = agent.check(design, _load(str(ex / "code_clean.json")), rules, context={"scan_id": "scan-1-clean"})
    print_summary(r1)
    _write(out_dir / "py_clean_arch_clean.report.json", r1.model_dump(mode="json"))
    print("Demo 2/3 - py-clean-arch bản cấy lỗi (fault_src) - lần quét thứ 2, dùng Knowledge của lần 1")
    r2 = agent.check(design, _load(str(ex / "code_faulty.json")), rules, context={"scan_id": "scan-2-faulty"})
    print_summary(r2)
    _write(out_dir / "py_clean_arch_faulty.report.json", r2.model_dump(mode="json"))
    _write(out_dir / "py_clean_arch_faulty.coordinator_input.json", to_coordinator_input(r2, "scan-1-clean"))
    _write(out_dir / "py_clean_arch_faulty.recommendation_request.json", to_recommendation_request(r2))

    ex2 = EXAMPLES / "ecommerce"
    print("Demo 3/3 - E-commerce (kiến trúc từ dữ liệu mẫu của AEA: system.puml + description.md)")
    r3 = ConsistencyCheckingAgent(knowledge=False).check(
        _load(str(ex2 / "design_architecture.json")), _load(str(ex2 / "code_envelope.json")),
        _load(str(ex2 / "rules.json")))
    print_summary(r3)
    _write(out_dir / "ecommerce.report.json", r3.model_dump(mode="json"))
    print(f"Kết quả JSON nằm trong thư mục: {out_dir}")
    print("Xem thêm demo 4 kịch bản: python -m consistency_agent scenarios")
    return 0


def cmd_scenarios(a: argparse.Namespace) -> int:
    """Demo 4 kịch bản trên hệ thống Quản lý thư viện (examples/scenarios)."""
    cfg = _llm_config(a, AgentConfig())
    sc = EXAMPLES / "scenarios"
    design, rules = _load(str(sc / "design_architecture.json")), _load(str(sc / "rules.json"))
    expected = _load(str(sc / "expected.json")) or {}
    out_dir = Path(a.output_dir)
    rows = []
    for name in ("1_match", "2_wrong", "3_missing", "4_junk"):
        agent = ConsistencyCheckingAgent(cfg, knowledge=False)
        r = agent.check(design, _load(str(sc / name / "code_envelope.json")), rules,
                        context={"project_id": f"library-{name}", "scan_id": f"scan-{name}"})
        print(f"\n################ Kịch bản {name} ################")
        print_summary(r, a.verbose)
        _write(out_dir / f"scenario_{name}.report.json", r.model_dump(mode="json"))
        sr = r.scenario_report
        exp = expected.get(name, {})
        ok = sr is not None and (sr.verdict, sr.primary_scenario) == (exp.get("verdict"), exp.get("primary_scenario"))
        rows.append((name, sr.verdict if sr else "-", sr.primary_scenario if sr else "-",
                     sr.counts if sr else {}, r.drift.drift_score, r.review.mode, ok))
    print("\n=== TỔNG HỢP 4 KỊCH BẢN ===")
    print(f"{'kịch bản':10} {'verdict':9} {'chính':8} {'khớp':>4} {'sai':>4} {'thiếu':>5} {'rác':>4} "
          f"{'drift':>6}  review  đúng kỳ vọng")
    for name, v, p, c, d, mode, ok in rows:
        print(f"{name:10} {v:9} {p:8} {c.get('match', 0):>4} {c.get('wrong', 0):>4} {c.get('missing', 0):>5} "
              f"{c.get('junk', 0):>4} {d:>6.3f}  {mode:6}  {'OK' if ok else 'KHÁC'}")
    print(f"Kết quả JSON: {out_dir}/scenario_*.report.json")
    return 0 if all(r[-1] for r in rows) else 1


def cmd_extract(a: argparse.Namespace) -> int:
    from .tools.sca_fallback import main as extract_main
    args = [a.source_path, "--run-id", a.run_id]
    if a.output:
        args += ["-o", a.output]
    if a.project_id:
        args += ["--project-id", a.project_id]
    return extract_main(args)


def cmd_feedback(a: argparse.Namespace) -> int:
    agent = ConsistencyCheckingAgent(knowledge=a.kb or None)
    if a.action == "accept":
        agent.accept_finding(a.project, a.fingerprint, a.note or "")
    elif a.action == "false-positive":
        agent.mark_false_positive(a.project, a.fingerprint, a.note or "")
    elif a.action == "confirm-mapping":
        agent.confirm_mapping(a.project, a.design_id, a.packages)
    elif a.action == "reject-mapping":
        agent.reject_mapping(a.project, a.design_id, a.packages[0])
    print(f"Đã lưu phản hồi '{a.action}' vào {agent.kb.path if agent.kb else '-'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="consistency_agent", description="Consistency Checking Agent")
    p.add_argument("--log-level", default="WARNING")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="Kiểm tra tính nhất quán")
    c.add_argument("--design", required=True, help="JSON ArchitectureGraph (đầu ra Architecture Extraction Agent)")
    c.add_argument("--code", required=True, help="JSON SourceCodeEnvelope (đầu ra Source Code Analysis Agent)")
    c.add_argument("--rules", help="JSON RuleSet (tùy chọn)")
    c.add_argument("--strategy", help="JSON chỉ thị chiến lược từ Coordinator (tùy chọn)")
    c.add_argument("--project-id")
    c.add_argument("--scan-id")
    c.add_argument("--commit")
    c.add_argument("--threshold", type=float, help="Ngưỡng confidence (mặc định 0.7)")
    c.add_argument("--kb", help="Đường dẫn SQLite Knowledge (mặc định data/cca_knowledge.sqlite)")
    c.add_argument("--no-kb", action="store_true", help="Tắt Knowledge (không học từ lần quét trước)")
    c.add_argument("-o", "--output", default="outputs/consistency_report.json")
    c.add_argument("--coordinator-output", help="Ghi thêm CoordinatorInput JSON")
    c.add_argument("--recommendation-output", help="Ghi thêm RecommendationRequest JSON")
    c.add_argument("-q", "--quiet", action="store_true")
    c.add_argument("-v", "--verbose", action="store_true", help="In nhận xét của reviewer cho mọi finding")
    _add_llm_args(c)
    c.set_defaults(func=cmd_check)

    sc = sub.add_parser("scenarios", help="Demo 4 kịch bản: khớp / code sai / code thiếu / code rác")
    sc.add_argument("--output-dir", default="outputs")
    sc.add_argument("-v", "--verbose", action="store_true")
    _add_llm_args(sc)
    sc.set_defaults(func=cmd_scenarios)

    d = sub.add_parser("demo", help="Chạy 3 kịch bản demo có sẵn")
    d.add_argument("--output-dir", default="outputs")
    d.set_defaults(func=cmd_demo)

    e = sub.add_parser("extract-code", help="Sinh SourceCodeEnvelope (định dạng SCA) bằng ast - dự phòng khi chưa có SCA")
    e.add_argument("source_path")
    e.add_argument("-o", "--output")
    e.add_argument("--project-id")
    e.add_argument("--run-id", default="run-fallback")
    e.set_defaults(func=cmd_extract)

    f = sub.add_parser("feedback", help="Human-in-the-loop: lưu quyết định vào Knowledge")
    f.add_argument("action", choices=["accept", "false-positive", "confirm-mapping", "reject-mapping"])
    f.add_argument("--project", required=True)
    f.add_argument("--fingerprint")
    f.add_argument("--note")
    f.add_argument("--design-id")
    f.add_argument("--packages", nargs="+")
    f.add_argument("--kb")
    f.set_defaults(func=cmd_feedback)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # console Windows (cp1252) không in được tiếng Việt
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined,union-attr]
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    logging.basicConfig(level=getattr(logging, a.log_level.upper(), logging.WARNING),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.cmd == "feedback":
        if a.action in ("accept", "false-positive") and not a.fingerprint:
            raise SystemExit("Cần --fingerprint")
        if a.action in ("confirm-mapping", "reject-mapping") and not (a.design_id and a.packages):
            raise SystemExit("Cần --design-id và --packages")
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
