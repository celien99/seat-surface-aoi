from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.paths import resolve_runtime_path


@dataclass(frozen=True)
class PcaProjectionResult:
    values: tuple[float, ...]
    version: str
    input_dim: int
    output_dim: int


@dataclass(frozen=True)
class PcaParameters:
    version: str
    mean: tuple[float, ...]
    components: tuple[tuple[float, ...], ...]


class PcaProjector:
    def __init__(self) -> None:
        self._cache: dict[str, PcaParameters] = {}

    def project(self, embedding: tuple[float, ...], pca_path: str, expected_version: str | None) -> PcaProjectionResult:
        params = self.load(pca_path)
        if expected_version is not None and params.version != expected_version:
            raise RuntimeError(f"PCA 版本不匹配: {params.version} != {expected_version}")
        if len(embedding) != len(params.mean):
            raise RuntimeError(f"PCA 输入维度不匹配: {len(embedding)} != {len(params.mean)}")
        mean = np.asarray(params.mean, dtype=np.float32)
        components = np.asarray(params.components, dtype=np.float32)
        if components.ndim != 2 or components.shape[1] != mean.size:
            actual_dim = int(components.shape[1]) if components.ndim == 2 else 0
            raise RuntimeError(f"PCA component 维度不匹配: {actual_dim} != {mean.size}")
        projected = (np.asarray(embedding, dtype=np.float32) - mean) @ components.T
        return PcaProjectionResult(
            values=tuple(float(value) for value in projected.tolist()),
            version=params.version,
            input_dim=len(embedding),
            output_dim=int(components.shape[0]),
        )

    def project_batch(
        self,
        embeddings: "np.ndarray | tuple[tuple[float, ...], ...]",
        pca_path: str,
        expected_version: str | None,
    ) -> "tuple[np.ndarray, str, int, int]":
        """批量 PCA 投影，用于空间 PatchCore 的多 patch embedding 同时降维。

        返回 (projected_matrix, version, input_dim, output_dim)。
        projected_matrix 形状为 (N, output_dim)，保持在 numpy 数组避免往返转换。
        """
        params = self.load(pca_path)
        if expected_version is not None and params.version != expected_version:
            raise RuntimeError(f"PCA 版本不匹配: {params.version} != {expected_version}")
        try:
            matrix = np.asarray(embeddings, dtype=np.float32)
        except ValueError as exc:
            raise RuntimeError("PCA 批量输入维度不一致") from exc
        if matrix.size == 0:
            raise RuntimeError("批量 PCA 输入为空")
        if matrix.ndim != 2:
            raise RuntimeError(f"PCA 批量输入必须是 2 维矩阵，实际: {matrix.ndim}")
        input_dim = int(matrix.shape[1])
        if input_dim != len(params.mean):
            raise RuntimeError(f"PCA 输入维度不匹配: {input_dim} != {len(params.mean)}")
        components = np.asarray(params.components, dtype=np.float32)
        if components.ndim != 2 or components.shape[1] != input_dim:
            actual_dim = int(components.shape[1]) if components.ndim == 2 else 0
            raise RuntimeError(f"PCA component 维度不匹配: {actual_dim} != {input_dim}")
        projected = (matrix - np.asarray(params.mean, dtype=np.float32)) @ components.T
        return projected, params.version, input_dim, int(components.shape[0])

    def load(self, path_value: str) -> PcaParameters:
        return self._load(path_value)

    def _load(self, path_value: str) -> PcaParameters:
        if path_value in self._cache:
            return self._cache[path_value]
        path = resolve_runtime_path(path_value)
        if not path.exists():
            raise ModelAssetUnavailableError(
                f"PCA 参数文件不存在: {path_value}",
                asset_kind="pca_parameters",
                asset_path=path_value,
                reason="missing",
            )
        if path.stat().st_size <= 1:
            raise ModelAssetUnavailableError(
                f"PCA 参数文件为空或仍是占位文件: {path_value}",
                asset_kind="pca_parameters",
                asset_path=path_value,
                reason="empty_or_placeholder",
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        params = self._parse(raw, path_value)
        self._cache[path_value] = params
        return params

    def _parse(self, raw: Any, source: str) -> PcaParameters:
        if not isinstance(raw, dict):
            raise RuntimeError(f"PCA 参数必须是 JSON object: {source}")
        version = self._str(raw.get("version"), "version")
        mean = self._float_tuple(raw.get("mean"), "mean")
        components_raw = raw.get("components")
        if not isinstance(components_raw, list) or not components_raw:
            raise RuntimeError("PCA components 必须是非空二维数组")
        components = tuple(self._float_tuple(component, "components") for component in components_raw)
        output_dim = len(components)
        if output_dim <= 0:
            raise RuntimeError("PCA 输出维度必须大于 0")
        for component in components:
            if len(component) != len(mean):
                raise RuntimeError(f"PCA component 维度不匹配: {len(component)} != {len(mean)}")
        return PcaParameters(version=version, mean=mean, components=components)

    def _str(self, value: Any, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"PCA {name} 必须是非空字符串")
        return value

    def _float_tuple(self, value: Any, name: str) -> tuple[float, ...]:
        if not isinstance(value, list) or not value:
            raise RuntimeError(f"PCA {name} 必须是非空数字数组")
        result = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in result):
            raise RuntimeError(f"PCA {name} 必须是有限数字")
        return result
