"""Adapter: SourceCodeEnvelope (Source Code Analysis Agent) -> CodeModel nội bộ.

Việc chính của adapter là DỰNG LẠI tên module đầy đủ và PHÂN GIẢI import, vì SCA hiện:
  * đặt ``from_module`` = tên file (``Path.stem``) -> mất thông tin package;
  * giữ nguyên chuỗi import thô (``.mapper``, ``..base``, ``src.x.y``).

Quy tắc phân giải (giống cách Python tìm module):
  1. Gốc mã nguồn của một file = thư mục tổ tiên gần nhất KHÔNG chứa ``__init__.py``.
  2. Import tương đối (``.x``) được tính từ package của file.
  3. Import tuyệt đối khớp module/package nội bộ -> ``internal``.
  4. Import bắt đầu bằng tên thư mục gốc (vd ``src.``) hoặc bằng một package nội bộ
     nhưng không có file tương ứng -> ``unresolved_internal`` (package "ma").
  5. Còn lại -> ``external`` (thư viện ngoài).
"""
from __future__ import annotations

import fnmatch
import logging
import posixpath
from typing import Any, Iterable, Optional

from ..schemas.inputs import SourceCodeEnvelopeIn
from ..schemas.internal import CodeDependency, CodeEvidence, CodeModel

log = logging.getLogger(__name__)

DEFAULT_IGNORE = [
    "tests", "tests.*", "test", "test.*", "*.tests", "*.tests.*", "test_*", "*.test_*",
    "conftest", "*.conftest", "setup", "docs.*", "examples.*", "scripts.*", "migrations.*",
    "*.migrations.*", "venv.*", ".venv.*",
]


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    if len(p) > 1 and p[1] == ":":  # ổ đĩa Windows "C:/..."
        p = p[0].lower() + p[1:]
    return posixpath.normpath(p)


def _common_dir(paths: list[str]) -> str:
    if not paths:
        return ""
    dirs = [posixpath.dirname(p) for p in paths]
    if len(dirs) == 1:
        return dirs[0]
    try:
        return posixpath.commonpath(dirs)
    except ValueError:  # trộn đường dẫn tuyệt đối/tương đối
        return ""


def _rel(path: str, base: str) -> str:
    if not base:
        return path
    try:
        return posixpath.relpath(path, base)
    except ValueError:
        return path


class _ModuleIndexer:
    """Tính tên module đầy đủ cho từng file .py."""

    def __init__(self, files: list[str], source_roots: Optional[list[str]] = None) -> None:
        self.files = [f for f in files if f.endswith(".py")]
        self.base = _common_dir(self.files)
        self.package_dirs = {posixpath.dirname(f) for f in self.files if posixpath.basename(f) == "__init__.py"}
        self.explicit_roots = [_norm(r) for r in (source_roots or [])]

    def _root_by_init(self, file: str) -> str:
        d = posixpath.dirname(file)
        while d in self.package_dirs:
            parent = posixpath.dirname(d)
            if parent == d:
                break
            d = parent
        return d

    def roots(self) -> dict[str, str]:
        if self.explicit_roots:
            out = {}
            for f in self.files:
                cands = [r for r in self.explicit_roots if f.startswith(r.rstrip("/") + "/")
                         or (not posixpath.isabs(r) and ("/" + r.strip("/") + "/") in f)]
                if cands:
                    r = max(cands, key=len)
                    if not posixpath.isabs(r) and not f.startswith(r):
                        idx = f.index("/" + r.strip("/") + "/")
                        r = f[: idx + len(r.strip("/")) + 1]
                    out[f] = r.rstrip("/")
                else:
                    out[f] = self._root_by_init(f)
            return out

        all_dirs = {posixpath.dirname(f) for f in self.files}
        # Dự án namespace (gần như không có __init__.py): lấy thư mục chung, ưu tiên "src/"
        if len(self.package_dirs) < max(1, 0.2 * len(all_dirs)):
            base = self.base
            src = posixpath.join(base, "src")
            root = src if all(f.startswith(src + "/") for f in self.files) else base
            return {f: root for f in self.files}

        per_file = {f: self._root_by_init(f) for f in self.files}
        root_set = set(per_file.values())
        fixed: dict[str, str] = {}
        for f, r in per_file.items():
            best = r
            for r0 in root_set:
                if r0 != r and r.startswith(r0.rstrip("/") + "/"):
                    child = posixpath.join(r0, r[len(r0):].strip("/").split("/")[0])
                    if child in self.package_dirs and len(r0) < len(best):
                        best = r0  # khoảng hở namespace bên trong cây package -> dùng gốc cao hơn
            fixed[f] = best
        return fixed

    def module_names(self) -> tuple[dict[str, str], dict[str, str]]:
        """Trả về (file -> module_name, file -> root)."""
        roots = self.roots()
        names: dict[str, str] = {}
        for f, r in roots.items():
            rel = _rel(f, r)
            if rel.endswith(".py"):
                rel = rel[:-3]
            parts = [p for p in rel.split("/") if p and p != "."]
            if parts and parts[-1] == "__init__":
                parts = parts[:-1]
            if not parts:  # __init__.py ngay tại gốc
                parts = [posixpath.basename(r) or "root"]
            names[f] = ".".join(parts)
        return names, roots


def _ignored(module: str, patterns: Iterable[str]) -> bool:
    return any(fnmatch.fnmatch(module, p) for p in patterns)


def to_code_model(data: Any, ignore_patterns: Optional[list[str]] = None,
                  source_roots: Optional[list[str]] = None) -> CodeModel:
    """Chuẩn hóa envelope của SCA thành CodeModel."""
    if hasattr(data, "model_dump"):
        data = data.model_dump()
    env = SourceCodeEnvelopeIn.model_validate(data)
    patterns = DEFAULT_IGNORE if ignore_patterns is None else ignore_patterns
    warnings: list[str] = list(env.warnings)

    files = {_norm(p) for p in env.evidence if p.endswith(".py")}
    files |= {_norm(d.evidence.file_path) for d in env.payload.dependencies}
    indexer = _ModuleIndexer(sorted(files), source_roots)
    file_to_module, file_to_root = indexer.module_names()
    base = indexer.base

    modules: dict[str, str] = {}
    is_pkg_init: dict[str, bool] = {}
    for f, m in file_to_module.items():
        if _ignored(m, patterns):
            continue
        if m in modules and modules[m] != _rel(f, base):
            warnings.append(f"Hai file cùng tên module '{m}': {modules[m]} và {_rel(f, base)}")
        modules[m] = _rel(f, base)
        is_pkg_init[m] = f.endswith("__init__.py")

    packages: set[str] = set()
    for m in modules:
        parts = m.split(".")
        for i in range(1, len(parts) + (1 if is_pkg_init.get(m) else 0)):
            packages.add(".".join(parts[:i]))
    tops = {m.split(".")[0] for m in modules}
    root_names = {posixpath.basename(r) for r in set(file_to_root.values()) if r}
    known = set(modules) | packages

    def longest_internal_prefix(name: str) -> Optional[str]:
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in known:
                return cand
        return None

    deps: list[CodeDependency] = []
    phantoms: set[str] = set()
    for d in env.payload.dependencies:
        f = _norm(d.evidence.file_path)
        src_mod = file_to_module.get(f)
        if src_mod is None or src_mod not in modules:
            continue  # file bị bỏ qua (vd tests)
        raw = d.to_module.strip()
        resolution = "external"
        target = raw

        if raw.startswith("."):
            dots = len(raw) - len(raw.lstrip("."))
            rest = raw[dots:]
            pkg_parts = src_mod.split(".") if is_pkg_init.get(src_mod) else src_mod.split(".")[:-1]
            up = dots - 1
            base_parts = pkg_parts[: len(pkg_parts) - up] if up <= len(pkg_parts) else []
            cand = ".".join([*base_parts, *([rest] if rest else [])])
            hit = longest_internal_prefix(cand) if cand else None
            if hit:
                resolution, target = "internal", hit
            else:
                resolution, target = "unresolved_internal", cand or raw
        else:
            hit = longest_internal_prefix(raw)
            first = raw.split(".")[0]
            if hit and hit.split(".")[0] in tops:
                resolution, target = "internal", hit
            elif first in root_names and "." in raw:
                stripped = raw.split(".", 1)[1]
                hit2 = longest_internal_prefix(stripped)
                if hit2:
                    resolution, target = "internal", hit2
                else:
                    resolution, target = "unresolved_internal", stripped
            elif first in tops:
                resolution, target = "unresolved_internal", raw

        if resolution == "unresolved_internal":
            phantoms.add(target.split(".")[0])
        if resolution != "external" and target == src_mod:
            continue  # tự import chính mình
        deps.append(CodeDependency(
            source_module=src_mod,
            target=target,
            resolution=resolution,  # type: ignore[arg-type]
            dependency_type=d.dependency_type,
            evidence=CodeEvidence(file_path=_rel(f, base), line_start=d.evidence.line_start,
                                  line_end=d.evidence.line_end, raw_import=raw),
        ))

    phantoms -= tops  # package có thật thì không phải "ma"
    if env.status != "SUCCESS":
        warnings.append(f"SCA trả trạng thái {env.status}: dữ liệu mã nguồn có thể chưa đầy đủ.")
    warnings.extend(f"SCA error: {e}" for e in env.errors)
    log.debug("CodeModel: %d modules, %d deps, phantoms=%s", len(modules), len(deps), phantoms)
    return CodeModel(
        project_id=env.project_id, run_id=env.run_id,
        source_roots=sorted({_rel(r, base) or "." for r in file_to_root.values()}),
        modules=modules, packages=packages, dependencies=deps, phantom_packages=phantoms,
        status=env.status, sca_confidence=env.confidence, warnings=warnings,
    )
