from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable


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


def _path_parts(raw_path: str | Path) -> tuple[str, ...]:
    value = str(raw_path).replace("\\", "/")
    return tuple(part for part in value.split("/") if part not in ("", "."))


def _config_relative_path(raw_path: str | Path) -> Path | None:
    parts = _path_parts(raw_path)
    if len(parts) >= 2 and parts[0] == "python_detector" and parts[1] == "config":
        return _relative_path(parts[2:])
    if parts and parts[0] == "config":
        return _relative_path(parts[1:])
    if parts and parts[0] in {"calibration", "roi"}:
        return _relative_path(parts)
    return None


def _append_candidate(candidates: list[Path], candidate: Path) -> None:
    if candidate not in candidates:
        candidates.append(candidate)


def _first_existing_or_first(candidates: Iterable[Path]) -> Path:
    items = list(candidates)
    for candidate in items:
        if candidate.exists():
            return candidate
    return items[0]


def _windows_absolute_path(value: str) -> bool:
    return (len(value) >= 3 and value[1] == ":" and value[2] in ("\\", "/")) or value.startswith("\\\\")


PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = _get_project_root()
DEFAULT_CONFIG_DIR = _get_project_root() / "python_detector" / "config"


def default_model_root(project_root: str | Path | None = None) -> Path | None:
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    if root.drive:
        return Path(root.drive + "\\") / "seat-aoi-model"
    return None


def resolve_runtime_path(raw_path: str | Path) -> Path:
    """Resolve deployment-time relative paths without depending on cwd.

    Production model assets default to ``<ProjectRoot drive>:\\seat-aoi-model``
    on Windows, while development keeps using ``PROJECT_ROOT/model``.
    """
    raw_value = str(raw_path)
    path = Path(raw_path)
    if path.is_absolute() or _windows_absolute_path(raw_value):
        return path

    parts = _path_parts(raw_path)
    if not parts:
        return path
    if parts[0] == "model":
        suffix = _relative_path(parts[1:])
        candidates: list[Path] = []
        model_root = default_model_root()
        if model_root is not None:
            _append_candidate(candidates, model_root / suffix)
        _append_candidate(candidates, PROJECT_ROOT / "model" / suffix)
        return _first_existing_or_first(candidates)

    project_relative = PROJECT_ROOT / _relative_path(parts)
    if project_relative.exists():
        return project_relative
    return path if path.exists() else project_relative


def resolve_package_path(base_dir: str | Path, raw_path: str | Path, *, strict_base_dir: bool = False) -> Path:
    """解析兼容仓库路径和安装后包内路径的资源路径。

    PyInstaller 部署后 python_detector/ 源目录不再存在，资源通过 PROJECT_ROOT
    和绝对路径解析。
    """
    path = Path(raw_path)
    if path.is_absolute() or _windows_absolute_path(str(raw_path)):
        return path

    base = Path(base_dir)
    parts = _path_parts(raw_path)
    normalized_relative = _relative_path(parts)
    candidates: list[Path] = []
    config_relative = _config_relative_path(raw_path)
    bundle_root = _pyinstaller_bundle_root()

    if strict_base_dir:
        _append_candidate(candidates, base / normalized_relative)
        if config_relative is not None:
            _append_candidate(candidates, base / config_relative)
            _append_candidate(candidates, base / "config" / config_relative)
        if parts and parts[0] == "python_detector":
            _append_candidate(candidates, base / _relative_path(parts[1:]))
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    # PyInstaller onefile 会把 __file__ 指向 _MEI 临时目录；生产配置和标定
    # 优先使用安装目录下可维护的 python_detector/config，打包资源仅作兜底。
    if _is_pyinstaller():
        if config_relative is not None:
            _append_candidate(candidates, DEFAULT_CONFIG_DIR / config_relative)
            _append_candidate(candidates, base / config_relative)
            _append_candidate(candidates, base / "config" / config_relative)
            if bundle_root is not None:
                _append_candidate(candidates, bundle_root / "python_detector" / "config" / config_relative)
                _append_candidate(candidates, bundle_root / normalized_relative)
        _append_candidate(candidates, base / normalized_relative)
        _append_candidate(candidates, PROJECT_ROOT / normalized_relative)
        if bundle_root is not None:
            _append_candidate(candidates, bundle_root / normalized_relative)
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    # 开发模式：保持原有搜索路径
    _append_candidate(candidates, base / normalized_relative)
    if config_relative is not None:
        _append_candidate(candidates, base / config_relative)
        _append_candidate(candidates, base / "config" / config_relative)
        _append_candidate(candidates, DEFAULT_CONFIG_DIR / config_relative)
        _append_candidate(candidates, PACKAGE_ROOT / "config" / config_relative)
    if parts and parts[0] == "python_detector":
        _append_candidate(candidates, base / _relative_path(parts[1:]))
        _append_candidate(candidates, PACKAGE_ROOT / _relative_path(parts[1:]))
    _append_candidate(candidates, PACKAGE_ROOT.parent / normalized_relative)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]
