from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from python_detector.config.recipe_schema import ModelConfig
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.onnx_runtime import create_onnx_session, numpy_module, run_first_input
from python_detector.pipeline.feature_builder import FeatureGroup


@dataclass(frozen=True)
class UnifiedEmbedding:
    values: tuple[float, ...]
    backend: str
    version: str
    layer_names: tuple[str, ...]
    input_shape_nchw: tuple[int, int, int, int] | None


@dataclass(frozen=True)
class SpatialEmbedding:
    """空间 PatchCore 嵌入：每个空间位置拼接多层特征的 patch embedding。"""

    patch_embeddings: "np.ndarray"
    """扁平化的 patch embedding 矩阵，形状 (H_out × W_out, patch_dim)。"""
    spatial_shape: tuple[int, int]
    """patch 网格空间尺寸 (H_out, W_out)。"""
    patch_dim: int
    """每个 patch embedding 的维数。"""
    backend: str
    version: str
    layer_names: tuple[str, ...]
    input_shape_nchw: tuple[int, int, int, int] | None
    layer_shapes: dict[str, tuple[int, int, int]]
    """各层原始 (C, H, W) 形状。"""

    __hash__ = None  # np.ndarray 不可哈希，显式禁用 hash


class EmbeddingExtractor:
    def __init__(self) -> None:
        self._onnx_sessions: dict[str, object] = {}

    def extract(self, feature_group: FeatureGroup, config: ModelConfig) -> UnifiedEmbedding:
        if config.embedding_backend == "statistical":
            values = self._statistical_embedding(feature_group, config.embedding_dim)
            return UnifiedEmbedding(
                values=values,
                backend="statistical",
                version=config.embedding_version,
                layer_names=config.embedding_layers,
                input_shape_nchw=feature_group.tensor_shape_nchw(),
            )
        if config.embedding_backend == "onnx_wideresnet50":
            values = self._onnx_embedding(feature_group, config)
            return UnifiedEmbedding(
                values=values,
                backend="onnx_wideresnet50",
                version=config.embedding_version,
                layer_names=config.embedding_layers,
                input_shape_nchw=feature_group.tensor_shape_nchw(),
            )
        raise RuntimeError(f"不支持的 embedding_backend: {config.embedding_backend}")

    def extract_spatial(self, feature_group: FeatureGroup, config: ModelConfig) -> SpatialEmbedding:
        """从空间 ONNX 模型提取多尺度 patch embedding。"""
        if config.embedding_backend != "onnx_wideresnet50":
            raise RuntimeError("spatial embedding 必须使用 embedding_backend=onnx_wideresnet50")
        if not config.spatial_layers:
            raise RuntimeError("spatial embedding 必须配置 spatial_layers")
        if not config.embedding_model_path:
            raise ModelAssetUnavailableError(
                "WideResNet50 spatial embedding 模型路径不能为空",
                asset_kind="onnx_model",
                asset_path="",
                reason="path_not_configured",
            )
        path = Path(config.embedding_model_path)
        if not path.exists():
            raise ModelAssetUnavailableError(
                f"WideResNet50 spatial embedding 模型文件不存在: {config.embedding_model_path}",
                asset_kind="onnx_model",
                asset_path=config.embedding_model_path,
                reason="missing",
            )
        if feature_group.tensor_nchw is None:
            raise RuntimeError("WideResNet50 spatial embedding 输入 tensor 缺失")

        np_runtime = numpy_module("WideResNet50 spatial embedding")
        session = self._cached_onnx_session(path, "WideResNet50 spatial embedding")
        tensor = np_runtime.asarray(feature_group.tensor_nchw, dtype=np_runtime.float32)
        outputs = run_first_input(session, tensor, "WideResNet50 spatial embedding")

        layer_names = config.spatial_layers if config.spatial_layers else config.embedding_layers
        if len(outputs) != len(layer_names):
            raise RuntimeError(f"ONNX 空间模型输出数 ({len(outputs)}) 与 spatial_layers ({len(layer_names)}) 不匹配")

        raw_layer_maps: dict[str, np.ndarray] = {}
        layer_shapes: dict[str, tuple[int, int, int]] = {}
        for name, output in zip(layer_names, outputs):
            arr = np.asarray(output, dtype=np.float32)
            if arr.ndim != 4:
                raise RuntimeError(f"空间层 {name} 输出必须是 4 维 [B,C,H,W]: 实际 {arr.ndim}")
            c_val, h_val, w_val = int(arr.shape[1]), int(arr.shape[2]), int(arr.shape[3])
            layer_shapes[name] = (c_val, h_val, w_val)
            raw_layer_maps[name] = arr[0]

        target_h = config.spatial_upsample_height
        target_w = config.spatial_upsample_width
        upsampled: dict[str, np.ndarray] = {}
        for name in layer_names:
            _c_val, h_val, w_val = layer_shapes[name]
            upsampled[name] = _upsample_nearest_array(raw_layer_maps[name], h_val, w_val, target_h, target_w)

        total_channels = sum(layer_shapes[name][0] for name in layer_names)
        stacked = np.concatenate([upsampled[name] for name in layer_names], axis=0)
        patch_matrix = np.moveaxis(stacked, 0, -1).reshape(target_h * target_w, total_channels)

        return SpatialEmbedding(
            patch_embeddings=patch_matrix,
            spatial_shape=(target_h, target_w),
            patch_dim=total_channels,
            backend="onnx_wideresnet50_spatial",
            version=config.embedding_version,
            layer_names=layer_names,
            input_shape_nchw=feature_group.tensor_shape_nchw(),
            layer_shapes=layer_shapes,
        )

    def _statistical_embedding(self, feature_group: FeatureGroup, embedding_dim: int) -> tuple[float, ...]:
        if feature_group.tensor_nchw is None:
            raise RuntimeError("embedding 输入 tensor 缺失")
        channels = np.asarray(feature_group.tensor_nchw, dtype=np.float32)[0]
        values: list[float] = []
        for channel in channels:
            if channel.size == 0:
                raise RuntimeError("embedding 输入通道为空")
            values.extend((float(channel.mean()), float(channel.std())))
        if len(values) < embedding_dim:
            values.extend([0.0] * (embedding_dim - len(values)))
        return tuple(values[:embedding_dim])

    def _onnx_embedding(self, feature_group: FeatureGroup, config: ModelConfig) -> tuple[float, ...]:
        if not config.embedding_model_path:
            raise ModelAssetUnavailableError(
                "WideResNet50 embedding 模型路径不能为空",
                asset_kind="onnx_model",
                asset_path="",
                reason="path_not_configured",
            )
        path = Path(config.embedding_model_path)
        if not path.exists():
            raise ModelAssetUnavailableError(
                f"WideResNet50 embedding 模型文件不存在: {config.embedding_model_path}",
                asset_kind="onnx_model",
                asset_path=config.embedding_model_path,
                reason="missing",
            )
        if feature_group.tensor_nchw is None:
            raise RuntimeError("WideResNet50 embedding 输入 tensor 缺失")
        np_runtime = numpy_module("WideResNet50 embedding")
        session = self._cached_onnx_session(path, "WideResNet50 embedding")
        tensor = np_runtime.asarray(feature_group.tensor_nchw, dtype=np_runtime.float32)
        outputs = run_first_input(session, tensor, "WideResNet50 embedding")
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if vector.size != config.embedding_dim:
            raise RuntimeError(f"embedding 维度不匹配: {vector.size} != {config.embedding_dim}")
        return tuple(float(value) for value in vector.tolist())

    def _cached_onnx_session(self, path: Path, purpose: str) -> object:
        cache_key = str(path)
        session = self._onnx_sessions.get(cache_key)
        if session is None:
            session = create_onnx_session(str(path), purpose)
            self._onnx_sessions[cache_key] = session
        return session

def _upsample_nearest_array(
    fm: np.ndarray,
    h_val: int,
    w_val: int,
    target_h: int,
    target_w: int,
) -> np.ndarray:
    """最近邻上采样 [C,H,W] -> [C,target_h,target_w]。"""
    if h_val == target_h and w_val == target_w:
        return fm
    row_indices = np.minimum((np.arange(target_h) * h_val // target_h).astype(np.intp), h_val - 1)
    col_indices = np.minimum((np.arange(target_w) * w_val // target_w).astype(np.intp), w_val - 1)
    return fm[:, row_indices[:, None], col_indices[None, :]]
