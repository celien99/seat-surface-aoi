from __future__ import annotations

import sys
from pathlib import Path


def _is_pyinstaller() -> bool:
    """PyInstaller 打包后 sys.frozen=True，且 sys.executable 指向打包的 .exe。"""
    return bool(getattr(sys, "frozen", False))


def _get_project_root() -> Path:
    """推导项目根目录。

    开发模式：PACKAGE_ROOT 的父目录（即仓库根目录）。
    PyInstaller 模式：从 sys.executable 向上两级（bin/seat_aoi_detector.exe → 项目根目录）。
    """
    if _is_pyinstaller():
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = _get_project_root()
DEFAULT_CONFIG_DIR = _get_project_root() / "python_detector" / "config"


def resolve_package_path(base_dir: str | Path, raw_path: str | Path) -> Path:
    """解析兼容仓库路径和安装后包内路径的资源路径。

    PyInstaller 部署后 python_detector/ 源目录不再存在，资源通过 PROJECT_ROOT
    和绝对路径解析。
    """
    path = Path(raw_path)
    if path.is_absolute():
        return path

    base = Path(base_dir)
    candidates = [base / path]

    # PyInstaller 部署后，包内路径不再有效，优先使用项目根目录
    if _is_pyinstaller():
        candidates.append(PROJECT_ROOT / "python_detector" / "config" / path.name)
        # 也尝试相对于基础目录查找
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    # 开发模式：保持原有搜索路径
    if path.parts and path.parts[0] == "python_detector":
        candidates.append(base / Path(*path.parts[1:]))
        candidates.append(PACKAGE_ROOT / Path(*path.parts[1:]))
    candidates.append(PACKAGE_ROOT.parent / path)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
