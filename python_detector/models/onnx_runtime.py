from __future__ import annotations

from pathlib import Path
from typing import Any


def create_onnx_session(model_path: str, purpose: str) -> Any:
    path = Path(model_path)
    if not path.exists():
        raise RuntimeError(f"{purpose} 模型文件不存在: {model_path}")
    if path.stat().st_size <= 1:
        raise RuntimeError(f"{purpose} 模型文件为空或仍是占位文件: {model_path}")
    try:
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"onnxruntime 未安装，无法启用 {purpose} 后端") from exc
    try:
        return ort.InferenceSession(str(path))
    except Exception as exc:
        raise RuntimeError(f"{purpose} ONNX session 创建失败: {exc}") from exc


def numpy_module(purpose: str) -> Any:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError(f"numpy 未安装，无法构建 {purpose} 输入") from exc
    return np


def run_first_input(session: Any, tensor: Any, purpose: str) -> list[Any]:
    inputs = session.get_inputs()
    if not inputs:
        raise RuntimeError(f"{purpose} 模型没有输入节点")
    outputs = session.run(None, {inputs[0].name: tensor})
    if not outputs:
        raise RuntimeError(f"{purpose} 模型输出为空")
    return list(outputs)
