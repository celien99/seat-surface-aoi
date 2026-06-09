from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any


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
        centered = [value - mean for value, mean in zip(embedding, params.mean)]
        projected: list[float] = []
        for component in params.components:
            if len(component) != len(centered):
                raise RuntimeError(f"PCA component 维度不匹配: {len(component)} != {len(centered)}")
            projected.append(sum(value * weight for value, weight in zip(centered, component)))
        return PcaProjectionResult(
            values=tuple(projected),
            version=params.version,
            input_dim=len(embedding),
            output_dim=len(projected),
        )

    def load(self, path_value: str) -> PcaParameters:
        return self._load(path_value)

    def _load(self, path_value: str) -> PcaParameters:
        if path_value in self._cache:
            return self._cache[path_value]
        path = Path(path_value)
        if not path.exists():
            raise RuntimeError(f"PCA 参数文件不存在: {path_value}")
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
