from __future__ import annotations

from pathlib import Path
from typing import Any

from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.paths import resolve_runtime_path


def create_onnx_session(model_path: str, purpose: str) -> Any:
    path = resolve_runtime_path(model_path)
    if not path.exists():
        raise ModelAssetUnavailableError(
            f"{purpose} 模型文件不存在: {model_path}",
            asset_kind="onnx_model",
            asset_path=model_path,
            reason="missing",
        )
    if path.stat().st_size <= 1 or _is_blank_placeholder(path):
        raise ModelAssetUnavailableError(
            f"{purpose} 模型文件为空或仍是占位文件: {model_path}",
            asset_kind="onnx_model",
            asset_path=model_path,
            reason="empty_or_placeholder",
        )
    try:
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise ModelAssetUnavailableError(
            f"onnxruntime 未安装，无法启用 {purpose} 后端",
            asset_kind="python_dependency",
            asset_path="onnxruntime",
            reason="dependency_missing",
        ) from exc
    try:
        return ort.InferenceSession(str(path))
    except Exception as exc:
        raise ModelAssetUnavailableError(
            f"{purpose} ONNX session 创建失败: {exc}",
            asset_kind="onnx_model",
            asset_path=model_path,
            reason="session_create_failed",
        ) from exc


def _is_blank_placeholder(path: Path) -> bool:
    if path.stat().st_size > 1024:
        return False
    try:
        return path.read_bytes().strip() in {b"", b"0"}
    except OSError:
        return False


def numpy_module(purpose: str) -> Any:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise ModelAssetUnavailableError(
            f"numpy 未安装，无法构建 {purpose} 输入",
            asset_kind="python_dependency",
            asset_path="numpy",
            reason="dependency_missing",
        ) from exc
    return np


def run_first_input(session: Any, tensor: Any, purpose: str) -> list[Any]:
    inputs = session.get_inputs()
    if not inputs:
        raise RuntimeError(f"{purpose} 模型没有输入节点")
    outputs = session.run(None, {inputs[0].name: tensor})
    if not outputs:
        raise RuntimeError(f"{purpose} 模型输出为空")
    return list(outputs)
