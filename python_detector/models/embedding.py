from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path

from python_detector.config.recipe_schema import ModelConfig
from python_detector.pipeline.feature_builder import FeatureGroup


@dataclass(frozen=True)
class UnifiedEmbedding:
    values: tuple[float, ...]
    backend: str
    version: str
    layer_names: tuple[str, ...]
    input_shape_nchw: tuple[int, int, int, int] | None


class EmbeddingExtractor:
    def extract(self, feature_group: FeatureGroup, config: ModelConfig) -> UnifiedEmbedding:
        if config.embedding_backend == "statistical":
            values = self._statistical_embedding(feature_group, config.embedding_dim)
            return UnifiedEmbedding(
                values=values,
                backend="statistical",
                version=config.embedding_version,
                layer_names=config.embedding_layers,
                input_shape_nchw=self._tensor_shape_nchw(feature_group),
            )
        if config.embedding_backend == "onnx_wideresnet50":
            values = self._onnx_embedding(feature_group, config)
            return UnifiedEmbedding(
                values=values,
                backend="onnx_wideresnet50",
                version=config.embedding_version,
                layer_names=config.embedding_layers,
                input_shape_nchw=self._tensor_shape_nchw(feature_group),
            )
        raise RuntimeError(f"不支持的 embedding_backend: {config.embedding_backend}")

    def _statistical_embedding(self, feature_group: FeatureGroup, embedding_dim: int) -> tuple[float, ...]:
        if feature_group.tensor_nchw is None:
            raise RuntimeError("embedding 输入 tensor 缺失")
        channels = feature_group.tensor_nchw[0]
        values: list[float] = []
        for channel in channels:
            flat = [float(value) for row in channel for value in row]
            if not flat:
                raise RuntimeError("embedding 输入通道为空")
            mean = sum(flat) / len(flat)
            variance = sum((value - mean) ** 2 for value in flat) / len(flat)
            values.extend((mean, math.sqrt(max(variance, 0.0))))
        if len(values) < embedding_dim:
            values.extend([0.0] * (embedding_dim - len(values)))
        return tuple(values[:embedding_dim])

    def _onnx_embedding(self, feature_group: FeatureGroup, config: ModelConfig) -> tuple[float, ...]:
        if not config.embedding_model_path:
            raise RuntimeError("WideResNet50 embedding 模型路径不能为空")
        path = Path(config.embedding_model_path)
        if not path.exists():
            raise RuntimeError(f"WideResNet50 embedding 模型文件不存在: {config.embedding_model_path}")
        if feature_group.tensor_nchw is None:
            raise RuntimeError("WideResNet50 embedding 输入 tensor 缺失")
        try:
            import numpy as np  # type: ignore
            import onnxruntime as ort  # type: ignore
        except Exception as exc:
            raise RuntimeError("onnxruntime/numpy 未安装，无法启用 WideResNet50 embedding 后端") from exc
        session = ort.InferenceSession(str(path))
        inputs = session.get_inputs()
        if not inputs:
            raise RuntimeError("WideResNet50 embedding 模型没有输入节点")
        tensor = np.asarray(feature_group.tensor_nchw, dtype=np.float32)
        outputs = session.run(None, {inputs[0].name: tensor})
        if not outputs:
            raise RuntimeError("WideResNet50 embedding 输出为空")
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if vector.size != config.embedding_dim:
            raise RuntimeError(f"embedding 维度不匹配: {vector.size} != {config.embedding_dim}")
        return tuple(float(value) for value in vector.tolist())

    def _tensor_shape_nchw(self, feature_group: FeatureGroup) -> tuple[int, int, int, int] | None:
        tensor = feature_group.tensor_nchw
        if tensor is None:
            return None
        batch = len(tensor)
        channels = len(tensor[0]) if batch else 0
        height = len(tensor[0][0]) if channels else 0
        width = len(tensor[0][0][0]) if height else 0
        return (batch, channels, height, width)
