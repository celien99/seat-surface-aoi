from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from python_detector.models.asset_errors import ModelAssetUnavailableError


@dataclass(frozen=True)
class PatchCoreBank:
    version: str
    model_family: str
    embedding_dim: int
    coreset_ratio: float
    vectors: tuple[tuple[float, ...], ...]
    pca_version: str | None
    faiss_enabled: bool = False


@dataclass(frozen=True)
class PatchCoreScore:
    anomaly_score: float
    nearest_distance: float
    knn_distances: tuple[float, ...]
    memory_bank_size: int
    embedding_dim: int
    backend: str
    version: str
    faiss_index_path: str | None = None
    fallback_reason: str | None = None


class PatchCoreKnnIndex:
    def __init__(self) -> None:
        self._cache: dict[str, PatchCoreBank] = {}

    def score(
        self,
        embedding: tuple[float, ...],
        memory_bank_path: str,
        knn_k: int,
        score_scale: float,
        expected_pca_version: str | None,
        faiss_index_path: str | None = None,
    ) -> PatchCoreScore:
        bank = self._load(memory_bank_path)
        if bank.model_family != "patchcore":
            raise RuntimeError(f"memory bank model_family 必须是 patchcore: {bank.model_family}")
        if len(embedding) != bank.embedding_dim:
            raise RuntimeError(f"PatchCore embedding 维度不匹配: {len(embedding)} != {bank.embedding_dim}")
        if expected_pca_version is not None and bank.pca_version not in (None, expected_pca_version):
            raise RuntimeError(f"PatchCore memory bank PCA 版本不匹配: {bank.pca_version} != {expected_pca_version}")
        k = min(knn_k, len(bank.vectors))
        if k <= 0:
            raise RuntimeError("PatchCore memory bank 为空")
        faiss_score = self._score_with_faiss(
            embedding,
            bank,
            k,
            score_scale,
            faiss_index_path,
        )
        if faiss_score is not None:
            return faiss_score
        fallback_reason = self._faiss_fallback_reason(bank, faiss_index_path)
        distances = sorted(self._euclidean(embedding, vector) for vector in bank.vectors)[:k]
        nearest = distances[0]
        anomaly_score = min(max(nearest * score_scale, 0.0), 1.0)
        return PatchCoreScore(
            anomaly_score=anomaly_score,
            nearest_distance=nearest,
            knn_distances=tuple(distances),
            memory_bank_size=len(bank.vectors),
            embedding_dim=bank.embedding_dim,
            backend="exact_knn",
            version=bank.version,
            faiss_index_path=faiss_index_path,
            fallback_reason=fallback_reason,
        )

    def load(self, path_value: str) -> PatchCoreBank:
        return self._load(path_value)

    def _load(self, path_value: str) -> PatchCoreBank:
        if path_value in self._cache:
            return self._cache[path_value]
        path = Path(path_value)
        if not path.exists():
            raise ModelAssetUnavailableError(
                f"PatchCore memory bank 不存在: {path_value}",
                asset_kind="patchcore_memory_bank",
                asset_path=path_value,
                reason="missing",
            )
        if path.stat().st_size <= 1:
            raise ModelAssetUnavailableError(
                f"PatchCore memory bank 为空或仍是占位文件: {path_value}",
                asset_kind="patchcore_memory_bank",
                asset_path=path_value,
                reason="empty_or_placeholder",
            )
        raw = json.loads(path.read_text(encoding="utf-8"))
        bank = self._parse(raw, path_value)
        self._cache[path_value] = bank
        return bank

    def _parse(self, raw: Any, source: str) -> PatchCoreBank:
        if not isinstance(raw, dict):
            raise RuntimeError(f"PatchCore memory bank 必须是 JSON object: {source}")
        version = self._str(raw.get("version"), "version")
        model_family = self._str(raw.get("model_family", "patchcore"), "model_family")
        vectors_raw = raw.get("vectors")
        if not isinstance(vectors_raw, list) or not vectors_raw:
            raise RuntimeError("PatchCore vectors 必须是非空二维数组")
        vectors = tuple(self._float_tuple(vector, "vectors") for vector in vectors_raw)
        embedding_dim = self._positive_int(raw.get("embedding_dim", len(vectors[0])), "embedding_dim")
        for vector in vectors:
            if len(vector) != embedding_dim:
                raise RuntimeError(f"PatchCore vector 维度不匹配: {len(vector)} != {embedding_dim}")
        coreset_ratio = self._ratio(raw.get("coreset_ratio", 1.0), "coreset_ratio")
        if coreset_ratio <= 0.0:
            raise RuntimeError("PatchCore coreset_ratio 必须大于 0")
        pca_version = raw.get("pca_version")
        if pca_version is not None and not isinstance(pca_version, str):
            raise RuntimeError("PatchCore pca_version 必须是字符串或 null")
        return PatchCoreBank(
            version=version,
            model_family=model_family,
            embedding_dim=embedding_dim,
            coreset_ratio=coreset_ratio,
            vectors=vectors,
            pca_version=pca_version,
            faiss_enabled=bool(raw.get("faiss_enabled", False)),
        )

    def _euclidean(self, left: tuple[float, ...], right: tuple[float, ...]) -> float:
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))

    def _score_with_faiss(
        self,
        embedding: tuple[float, ...],
        bank: PatchCoreBank,
        knn_k: int,
        score_scale: float,
        faiss_index_path: str | None,
    ) -> PatchCoreScore | None:
        if not faiss_index_path:
            return None
        path = Path(faiss_index_path)
        if not path.exists() or path.stat().st_size <= 1:
            return None
        try:
            import faiss  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return None
        try:
            index = faiss.read_index(str(path))
            index_dim = int(getattr(index, "d"))
            if index_dim != len(embedding):
                return None
            query = np.asarray([embedding], dtype=np.float32)
            distances_raw, _indices = index.search(query, knn_k)
            distances = tuple(math.sqrt(max(float(value), 0.0)) for value in distances_raw[0].tolist())
            distances = tuple(value for value in distances if math.isfinite(value))
            if not distances:
                return None
            nearest = distances[0]
            anomaly_score = min(max(nearest * score_scale, 0.0), 1.0)
            return PatchCoreScore(
                anomaly_score=anomaly_score,
                nearest_distance=nearest,
                knn_distances=distances,
                memory_bank_size=len(bank.vectors),
                embedding_dim=bank.embedding_dim,
                backend="faiss",
                version=bank.version,
                faiss_index_path=faiss_index_path,
                fallback_reason=None,
            )
        except Exception:
            return None

    def _faiss_fallback_reason(self, bank: PatchCoreBank, faiss_index_path: str | None) -> str | None:
        if not bank.faiss_enabled and not faiss_index_path:
            return None
        if not faiss_index_path:
            return "faiss_index_path_not_configured"
        path = Path(faiss_index_path)
        if not path.exists():
            return "faiss_index_missing"
        if path.stat().st_size <= 1:
            return "faiss_index_empty_or_placeholder"
        try:
            import faiss  # type: ignore  # noqa: F401
            import numpy  # type: ignore  # noqa: F401
        except Exception:
            return "faiss_unavailable"
        return "faiss_load_or_search_failed"

    def _str(self, value: Any, name: str) -> str:
        if not isinstance(value, str) or not value:
            raise RuntimeError(f"PatchCore {name} 必须是非空字符串")
        return value

    def _positive_int(self, value: Any, name: str) -> int:
        if not isinstance(value, int) or value <= 0:
            raise RuntimeError(f"PatchCore {name} 必须是正整数")
        return value

    def _ratio(self, value: Any, name: str) -> float:
        if not isinstance(value, (int, float)):
            raise RuntimeError(f"PatchCore {name} 必须是数字")
        result = float(value)
        if result < 0.0 or result > 1.0:
            raise RuntimeError(f"PatchCore {name} 必须在 [0, 1] 范围内")
        return result

    def _float_tuple(self, value: Any, name: str) -> tuple[float, ...]:
        if not isinstance(value, list) or not value:
            raise RuntimeError(f"PatchCore {name} 必须是非空数字数组")
        result = tuple(float(item) for item in value)
        if not all(math.isfinite(item) for item in result):
            raise RuntimeError(f"PatchCore {name} 必须是有限数字")
        return result
