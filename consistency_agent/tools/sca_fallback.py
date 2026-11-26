"""Bộ trích xuất phụ thuộc DỰ PHÒNG, sinh JSON đúng định dạng SourceCodeEnvelope của SCA.

Mục đích: chạy demo / test CCA trên máy chưa cài được ``tree-sitter`` (SCA thật cần
tree-sitter + tree-sitter-python). Đây KHÔNG phải thay thế SCA: nó dùng module ``ast``
có sẵn của Python và chỉ bắt ``import`` / ``from ... import`` giống SCA phiên bản hiện tại.

Khác biệt nhỏ so với SCA (đã ghi trong README):
  * Bắt được ``import x as y`` (SCA hiện bỏ sót vì node ``aliased_import``).
  * Đường dẫn file ghi tương đối so với thư mục quét (SCA ghi đường dẫn tuyệt đối);
    CCA chấp nhận cả hai.

Dùng:  python -m consistency_agent.tools.sca_fallback <thu_muc_ma_nguon> -o code.json
"""
from __future__ import annotations

import argparse
import ast
import json
import logging
import sys
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".tox", "build", "dist"}


def _iter_py(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        out.append(p)
    return out


def extract_file(path: Path, display: str) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
    except SyntaxError as exc:
        raise ValueError(f"SyntaxError {display}:{exc.lineno}") from exc
    deps = []
    for node in ast.walk(tree):
        targets: list[str] = []
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.ImportFrom):
            targets = ["." * node.level + (node.module or "")]
        elif isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        for t in targets:
            deps.append({
                "from_module": path.stem,
                "to_module": t,
                "dependency_type": "import",
                "evidence": {"file_path": display, "line_start": node.lineno,
                             "line_end": getattr(node, "end_lineno", node.lineno) or node.lineno},
            })
    return deps


def build_envelope(source_path: str | Path, project_id: Optional[str] = None,
                   run_id: str = "run-fallback", prefix: Optional[str] = None) -> dict:
    root = Path(source_path).resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Không tìm thấy thư mục mã nguồn: {root}")
    prefix = root.name if prefix is None else prefix
    files = _iter_py(root)
    deps: list[dict] = []
    errors: list[str] = []
    evidence: list[str] = []
    for f in files:
        display = "/".join(filter(None, [prefix, f.relative_to(root).as_posix()]))
        evidence.append(display)
        try:
            deps.extend(extract_file(f, display))
        except ValueError as exc:
            errors.append(str(exc))
    status = "SUCCESS" if not errors else ("PARTIAL" if deps else "FAILED")
    return {
        "schema_version": "1.0",
        "project_id": project_id or root.name,
        "run_id": run_id,
        "agent_name": "Source Code Analysis",
        "status": status,
        "confidence": 0.95 if not errors else 0.7,
        "evidence": evidence,
        "payload": {"dependencies": deps},
        "warnings": ["Sinh bởi consistency_agent.tools.sca_fallback (ast), không phải SCA tree-sitter."],
        "errors": errors,
    }


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Sinh SourceCodeEnvelope (định dạng SCA) bằng ast.")
    ap.add_argument("source_path")
    ap.add_argument("-o", "--output", help="File JSON đầu ra (mặc định in ra màn hình)")
    ap.add_argument("--project-id")
    ap.add_argument("--run-id", default="run-fallback")
    args = ap.parse_args(argv)
    env = build_envelope(args.source_path, args.project_id, args.run_id)
    text = json.dumps(env, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"Đã ghi {args.output}: {len(env['payload']['dependencies'])} dependencies")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
