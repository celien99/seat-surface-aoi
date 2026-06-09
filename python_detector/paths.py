from __future__ import annotations

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_DIR = PACKAGE_ROOT / "config"


def resolve_package_path(base_dir: str | Path, raw_path: str | Path) -> Path:
    """解析兼容仓库路径和安装后包内路径的资源路径。"""
    path = Path(raw_path)
    if path.is_absolute():
        return path

    base = Path(base_dir)
    candidates = [base / path]
    if path.parts and path.parts[0] == "python_detector":
        candidates.append(base / Path(*path.parts[1:]))
        candidates.append(PACKAGE_ROOT / Path(*path.parts[1:]))
    candidates.append(PACKAGE_ROOT.parent / path)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
