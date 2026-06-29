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


def _pyinstaller_bundle_root() -> Path | None:
    root = getattr(sys, "_MEIPASS", None)
    return Path(root).resolve() if root else None


def _relative_path(parts: tuple[str, ...]) -> Path:
    return Path(*parts) if parts else Path()


def _config_relative_path(path: Path) -> Path | None:
    parts = path.parts
    if len(parts) >= 2 and parts[0] == "python_detector" and parts[1] == "config":
        return _relative_path(parts[2:])
    if parts and parts[0] == "config":
        return _relative_path(parts[1:])
    if parts and parts[0] in {"calibration", "roi"}:
        return path
    return None


def _append_candidate(candidates: list[Path], candidate: Path) -> None:
    if candidate not in candidates:
        candidates.append(candidate)


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
    candidates: list[Path] = []
    config_relative = _config_relative_path(path)
    bundle_root = _pyinstaller_bundle_root()

    # PyInstaller onefile 会把 __file__ 指向 _MEI 临时目录；生产配置和标定
    # 优先使用安装目录下可维护的 python_detector/config，打包资源仅作兜底。
    if _is_pyinstaller():
        if config_relative is not None:
            _append_candidate(candidates, DEFAULT_CONFIG_DIR / config_relative)
            _append_candidate(candidates, base / config_relative)
            _append_candidate(candidates, base / "config" / config_relative)
            if bundle_root is not None:
                _append_candidate(candidates, bundle_root / "python_detector" / "config" / config_relative)
                _append_candidate(candidates, bundle_root / path)
        _append_candidate(candidates, base / path)
        _append_candidate(candidates, PROJECT_ROOT / path)
        if bundle_root is not None:
            _append_candidate(candidates, bundle_root / path)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    # 开发模式：保持原有搜索路径
    _append_candidate(candidates, base / path)
    if config_relative is not None:
        _append_candidate(candidates, base / config_relative)
        _append_candidate(candidates, base / "config" / config_relative)
        _append_candidate(candidates, DEFAULT_CONFIG_DIR / config_relative)
        _append_candidate(candidates, PACKAGE_ROOT / "config" / config_relative)
    if path.parts and path.parts[0] == "python_detector":
        _append_candidate(candidates, base / _relative_path(path.parts[1:]))
        _append_candidate(candidates, PACKAGE_ROOT / _relative_path(path.parts[1:]))
    _append_candidate(candidates, PACKAGE_ROOT.parent / path)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
