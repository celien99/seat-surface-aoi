from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import numpy as np

from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.paths import resolve_runtime_path


@dataclass(frozen=True)
class PatchCoreThresholds:
    """PatchCore 判定阈值，来自训练/校准产物而不是在线配方。"""

    recheck_score: float
    ng_score: float
    source: str = "normal_bootstrap_quantile"
    normal_quantile_recheck: float | None = None
    normal_quantile_ng: float | None = None


@dataclass(frozen=True)
class PatchCoreBank:
    version: str
    model_family: str
    embedding_dim: int
    coreset_ratio: float
    vectors_path: str
    vectors: np.ndarray
    pca_version: str | None
    faiss_enabled: bool = False
    metadata: dict[str, Any] | None = None
    distance_mean: float | None = None
    distance_std: float | None = None
    distance_p99: float | None = None
    """held-out 正常样本到 bank 最近邻距离的 p99 分位数"""
    thresholds: PatchCoreThresholds | None = None




@dataclass(frozen=True)
class PatchCoreScore:
    anomaly_score: float
    nearest_distance: float
    knn_distances: tuple[float, ...]
    memory_bank_size: int
    embedding_dim: int
    backend: str
    version: str
    thresholds: PatchCoreThresholds
    faiss_index_path: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True)
class SpatialAnomalyScore:
    """空间 PatchCore 异常分数，包含二维 anomaly map 和最近邻距离图。"""

    anomaly_map: "np.ndarray"
    spatial_shape: tuple[int, int]
    nearest_distances: "np.ndarray"
    memory_bank_size: int
    embedding_dim: int
    backend: str
    version: str
    thresholds: PatchCoreThresholds
    faiss_index_path: str | None = None
    fallback_reason: str | None = None

    __hash__ = None  # np.ndarray 不可哈希，显式禁用 hash


def _calibrated_score(
    nearest: "np.ndarray | float",
    distance_mean: float,
    distance_p99: float,
) -> "np.ndarray | float":
    """用训练数据 held-out 距离分位数做归一化，tanh 压缩尾部。

        x = (nearest - mean) / (2 * max(p99 - mean, ε))
        anomaly_score = tanh(x)

    tanh 特性：均值附近近似线性，尾部自然饱和。score=0.76 对应 p99，
    避免 65536 patch 空间模式下正常样本尾部噪声被误判为 NG。
    distance_mean 和 distance_p99 来自 memory bank JSON。
    """
    span = 2.0 * max(distance_p99 - distance_mean, 1e-6)
    x = (nearest - distance_mean) / span
    return np.tanh(x)


def _require_thresholds(bank: PatchCoreBank, memory_bank_path: str) -> PatchCoreThresholds:
    if bank.thresholds is None:
        raise ModelAssetUnavailableError(
            "PatchCore memory bank 缺少 thresholds 判定阈值，请重新运行训练/校准工具",
            asset_kind="patchcore_thresholds",
            asset_path=memory_bank_path,
            reason="thresholds_missing",
        )
    return bank.thresholds


class PatchCoreKnnIndex:
    def __init__(self) -> None:
        self._cache: dict[str, PatchCoreBank] = {}
        self._faiss_index_cache: dict[str, tuple[int, int, Any]] = {}

    def clear_caches(self) -> None:
        """清除所有内部缓存，用于配方热更新或长期运行后释放内存。"""
        self._cache.clear()
        self._faiss_index_cache.clear()

    def score(
        self,
        embedding: tuple[float, ...],
        memory_bank_path: str,
        knn_k: int,
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
        if bank.distance_mean is None or bank.distance_p99 is None:
            raise RuntimeError("PatchCore memory bank 缺少 distance_mean/distance_p99 校准统计量，请运行 calibrate 子命令")
        thresholds = _require_thresholds(bank, memory_bank_path)
        k = min(knn_k, len(bank.vectors))
        if k <= 0:
            raise RuntimeError("PatchCore memory bank 为空")
        faiss_score = self._score_with_faiss(
            embedding,
            bank,
            thresholds,
            k,
            faiss_index_path,
        )
        if faiss_score is not None:
            return faiss_score
        fallback_reason = self._faiss_fallback_reason(bank, faiss_index_path)
        distances = _topk_distances(np.asarray([embedding], dtype=np.float32), np.asarray(bank.vectors, dtype=np.float32), k)[0]
        nearest = float(distances[0])
        anomaly_score = min(max(_calibrated_score(nearest, bank.distance_mean, bank.distance_p99), 0.0), 1.0)
        return PatchCoreScore(
            anomaly_score=anomaly_score,
            nearest_distance=nearest,
            knn_distances=tuple(float(value) for value in distances.tolist()),
            memory_bank_size=len(bank.vectors),
            embedding_dim=bank.embedding_dim,
            backend="exact_knn",
            version=bank.version,
            thresholds=thresholds,
            faiss_index_path=faiss_index_path,
            fallback_reason=fallback_reason,
        )

    def score_spatial(
        self,
        patch_embeddings: "np.ndarray",
        spatial_shape: tuple[int, int],
        memory_bank_path: str,
        knn_k: int,
        expected_pca_version: str | None,
        faiss_index_path: str | None = None,
    ) -> SpatialAnomalyScore:
        """对每个空间 patch 做 KNN 评分，返回二维异常热力图。"""
        bank = self._load(memory_bank_path)
        if bank.model_family != "patchcore":
            raise RuntimeError(f"memory bank model_family 必须是 patchcore: {bank.model_family}")
        if patch_embeddings.size == 0:
            raise RuntimeError("patch_embeddings 为空")
        if patch_embeddings.ndim != 2:
            raise RuntimeError(f"patch_embeddings 必须是 2 维矩阵，实际: {patch_embeddings.ndim}")
        if patch_embeddings.shape[1] != bank.embedding_dim:
            raise RuntimeError(f"PatchCore patch embedding 维度不匹配: {patch_embeddings.shape[1]} != {bank.embedding_dim}")
        if expected_pca_version is not None and bank.pca_version not in (None, expected_pca_version):
            raise RuntimeError(f"PatchCore memory bank PCA 版本不匹配: {bank.pca_version} != {expected_pca_version}")
        if bank.distance_mean is None or bank.distance_p99 is None:
            raise RuntimeError("PatchCore memory bank 缺少 distance_mean/distance_p99 校准统计量，请运行 calibrate 子命令")
        thresholds = _require_thresholds(bank, memory_bank_path)
        k = min(knn_k, len(bank.vectors))
        if k <= 0:
            raise RuntimeError("PatchCore memory bank 为空")

        h_out, w_out = spatial_shape
        if patch_embeddings.shape[0] != h_out * w_out:
            raise RuntimeError(f"patch_embeddings 数量 ({patch_embeddings.shape[0]}) 与 spatial_shape {spatial_shape} 不匹配")

        faiss_result = self._score_spatial_faiss(
            patch_embeddings, spatial_shape, bank, thresholds, k, faiss_index_path
        )
        if faiss_result is not None:
            return faiss_result

        fallback_reason = self._faiss_fallback_reason(bank, faiss_index_path)
        return self._score_spatial_exact(
            patch_embeddings,
            spatial_shape,
            bank,
            thresholds,
            k,
            faiss_index_path,
            fallback_reason,
        )

    def load(self, path_value: str) -> PatchCoreBank:
        return self._load(path_value)

    def _score_spatial_faiss(
        self,
        patch_embeddings: "np.ndarray",
        spatial_shape: tuple[int, int],
        bank: PatchCoreBank,
        thresholds: PatchCoreThresholds,
        knn_k: int,
        faiss_index_path: str | None,
    ) -> SpatialAnomalyScore | None:
        if not faiss_index_path:
            return None
        path = resolve_runtime_path(faiss_index_path)
        if not path.exists() or path.stat().st_size <= 1:
            return None
        try:
            index = self._load_faiss_index(path)
            index_dim = int(getattr(index, "d"))
            if index_dim != bank.embedding_dim:
                return None
            queries = np.asarray(patch_embeddings, dtype=np.float32)
            distances_raw, _indices = index.search(queries, knn_k)
            if distances_raw.ndim != 2 or distances_raw.shape[0] != queries.shape[0]:
                return None
            nearest = _nearest_finite_distances(distances_raw)
            score_array = np.clip(_calibrated_score(nearest, bank.distance_mean, bank.distance_p99), 0.0, 1.0)
            h_out, w_out = spatial_shape
            return SpatialAnomalyScore(
                anomaly_map=score_array.reshape(h_out, w_out),
                spatial_shape=spatial_shape,
                nearest_distances=nearest.reshape(h_out, w_out),
                memory_bank_size=len(bank.vectors),
                embedding_dim=bank.embedding_dim,
                backend="faiss",
                version=bank.version,
                thresholds=thresholds,
                faiss_index_path=faiss_index_path,
                fallback_reason=None,
            )
        except Exception:
            return None

    def _score_spatial_exact(
        self,
        patch_embeddings: "np.ndarray",
        spatial_shape: tuple[int, int],
        bank: PatchCoreBank,
        thresholds: PatchCoreThresholds,
        knn_k: int,
        faiss_index_path: str | None,
        fallback_reason: str | None,
    ) -> SpatialAnomalyScore:
        h_out, w_out = spatial_shape
        queries = np.asarray(patch_embeddings, dtype=np.float32)
        nearest = _topk_distances(queries, np.asarray(bank.vectors, dtype=np.float32), knn_k)[:, 0]
        score_array = np.clip(_calibrated_score(nearest, bank.distance_mean, bank.distance_p99), 0.0, 1.0)
        return SpatialAnomalyScore(
            anomaly_map=score_array.reshape(h_out, w_out),
            spatial_shape=spatial_shape,
            nearest_distances=nearest.reshape(h_out, w_out),
            memory_bank_size=len(bank.vectors),
            embedding_dim=bank.embedding_dim,
            backend="exact_knn",
            version=bank.version,
            thresholds=thresholds,
            faiss_index_path=faiss_index_path,
            fallback_reason=fallback_reason,
        )

    def _load(self, path_value: str) -> PatchCoreBank:
        if path_value in self._cache:
            return self._cache[path_value]
        path = resolve_runtime_path(path_value)
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
        bank = self._parse(raw, path)
        self._cache[path_value] = bank
        return bank

    def _parse(self, raw: Any, source: Path) -> PatchCoreBank:
        if not isinstance(raw, dict):
            raise RuntimeError(f"PatchCore memory bank 必须是 JSON object: {source}")
        version = self._str(raw.get("version"), "version")
        model_family = self._str(raw.get("model_family", "patchcore"), "model_family")
        if "vectors" in raw:
            raise RuntimeError("PatchCore memory bank 不再支持 JSON 内嵌 vectors，请使用 vectors_path 指向 .npy 向量矩阵")
        embedding_dim = self._positive_int(raw.get("embedding_dim"), "embedding_dim")
        vectors_path_value = self._str(raw.get("vectors_path"), "vectors_path")
        vectors_path = _resolve_vectors_path(vectors_path_value, source.parent)
        vectors = _load_vectors_npy(vectors_path, embedding_dim)
        expected_count = raw.get("vector_count")
        if expected_count is not None and int(expected_count) != int(vectors.shape[0]):
            raise RuntimeError(f"PatchCore vector_count 与 .npy 向量数不匹配: {expected_count} != {vectors.shape[0]}")
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
            vectors_path=str(vectors_path),
            vectors=vectors,
            pca_version=pca_version,
            faiss_enabled=bool(raw.get("faiss_enabled", False)),
            metadata=raw.get("metadata") if isinstance(raw.get("metadata"), dict) else None,
            distance_mean=raw.get("distance_mean") if isinstance(raw.get("distance_mean"), (int, float)) else None,
            distance_std=raw.get("distance_std") if isinstance(raw.get("distance_std"), (int, float)) else None,
            distance_p99=raw.get("distance_p99") if isinstance(raw.get("distance_p99"), (int, float)) else None,
            thresholds=_parse_thresholds(raw.get("thresholds"), source),
        )

    def _score_with_faiss(
        self,
        embedding: tuple[float, ...],
        bank: PatchCoreBank,
        thresholds: PatchCoreThresholds,
        knn_k: int,
        faiss_index_path: str | None,
    ) -> PatchCoreScore | None:
        if not faiss_index_path:
            return None
        path = resolve_runtime_path(faiss_index_path)
        if not path.exists() or path.stat().st_size <= 1:
            return None
        try:
            index = self._load_faiss_index(path)
            index_dim = int(getattr(index, "d"))
            if index_dim != len(embedding):
                return None
            query = np.asarray([embedding], dtype=np.float32)
            distances_raw, _indices = index.search(query, knn_k)
            distances = _finite_row_distances(distances_raw[0])
            if distances.size == 0:
                return None
            nearest = float(distances[0])
            anomaly_score = min(max(_calibrated_score(nearest, bank.distance_mean, bank.distance_p99), 0.0), 1.0)
            return PatchCoreScore(
                anomaly_score=anomaly_score,
                nearest_distance=nearest,
                knn_distances=tuple(float(value) for value in distances.tolist()),
                memory_bank_size=len(bank.vectors),
                embedding_dim=bank.embedding_dim,
                backend="faiss",
                version=bank.version,
                thresholds=thresholds,
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
        path = resolve_runtime_path(faiss_index_path)
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

    def _load_faiss_index(self, path: Path) -> Any:
        stat = path.stat()
        cache_key = str(path)
        cached = self._faiss_index_cache.get(cache_key)
        if cached is not None:
            cached_size, cached_mtime_ns, cached_index = cached
            if cached_size == stat.st_size and cached_mtime_ns == stat.st_mtime_ns:
                return cached_index
        import faiss  # type: ignore

        index = faiss.read_index(str(path))
        self._faiss_index_cache[cache_key] = (int(stat.st_size), int(stat.st_mtime_ns), index)
        return index

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

def _resolve_vectors_path(path_value: str, bank_dir: Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return bank_dir / path


def _parse_thresholds(raw: Any, source: Path) -> PatchCoreThresholds | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError(f"PatchCore thresholds 必须是 JSON object: {source}")
    recheck_score = _threshold_ratio(raw.get("recheck_score"), "thresholds.recheck_score", source)
    ng_score = _threshold_ratio(raw.get("ng_score"), "thresholds.ng_score", source)
    if recheck_score > ng_score:
        raise RuntimeError(
            f"PatchCore thresholds.recheck_score 不能大于 ng_score: {recheck_score} > {ng_score}"
        )
    source_name = raw.get("source", "normal_bootstrap_quantile")
    if not isinstance(source_name, str) or not source_name:
        raise RuntimeError(f"PatchCore thresholds.source 必须是非空字符串: {source}")
    return PatchCoreThresholds(
        recheck_score=recheck_score,
        ng_score=ng_score,
        source=source_name,
        normal_quantile_recheck=_optional_threshold_ratio(raw.get("normal_quantile_recheck"), source),
        normal_quantile_ng=_optional_threshold_ratio(raw.get("normal_quantile_ng"), source),
    )


def _threshold_ratio(value: Any, name: str, source: Path) -> float:
    if not isinstance(value, (int, float)):
        raise RuntimeError(f"PatchCore {name} 必须是 [0, 1] 数字: {source}")
    result = float(value)
    if result < 0.0 or result > 1.0 or not np.isfinite(result):
        raise RuntimeError(f"PatchCore {name} 必须是 [0, 1] 有限数字: {result}")
    return result


def _optional_threshold_ratio(value: Any, source: Path) -> float | None:
    if value is None:
        return None
    return _threshold_ratio(value, "thresholds.normal_quantile", source)


def _load_vectors_npy(path: Path, expected_dim: int) -> np.ndarray:
    if not path.exists():
        raise ModelAssetUnavailableError(
            f"PatchCore vectors .npy 不存在: {path}",
            asset_kind="patchcore_vectors",
            asset_path=str(path),
            reason="missing",
        )
    if path.stat().st_size <= 1:
        raise ModelAssetUnavailableError(
            f"PatchCore vectors .npy 为空或仍是占位文件: {path}",
            asset_kind="patchcore_vectors",
            asset_path=str(path),
            reason="empty_or_placeholder",
        )
    vectors = np.load(str(path), mmap_mode="r", allow_pickle=False)
    if vectors.ndim != 2:
        raise RuntimeError(f"PatchCore vectors .npy 必须是 2 维矩阵: {vectors.shape}")
    if vectors.shape[0] <= 0:
        raise RuntimeError("PatchCore vectors .npy 不能为空")
    if vectors.shape[1] != expected_dim:
        raise RuntimeError(f"PatchCore vectors .npy 维度不匹配: {vectors.shape[1]} != {expected_dim}")
    if vectors.dtype != np.float32:
        raise RuntimeError(f"PatchCore vectors .npy 必须是 float32，实际: {vectors.dtype}")
    if not bool(np.isfinite(vectors).all()):
        raise RuntimeError("PatchCore vectors .npy 包含非有限值")
    return vectors


def _topk_distances(queries: np.ndarray, bank_vectors: np.ndarray, knn_k: int, chunk_size: int = 256) -> np.ndarray:
    if queries.ndim != 2 or bank_vectors.ndim != 2 or queries.shape[1] != bank_vectors.shape[1]:
        raise RuntimeError(f"PatchCore KNN 维度不匹配: {queries.shape} vs {bank_vectors.shape}")
    k = min(knn_k, bank_vectors.shape[0])
    if k <= 0:
        raise RuntimeError("PatchCore memory bank 为空")
    bank_norm = np.sum(np.square(bank_vectors, dtype=np.float32), axis=1)
    result = np.empty((queries.shape[0], k), dtype=np.float32)
    for start in range(0, queries.shape[0], chunk_size):
        end = min(start + chunk_size, queries.shape[0])
        chunk = queries[start:end]
        query_norm = np.sum(np.square(chunk, dtype=np.float32), axis=1, keepdims=True)
        distances_sq = query_norm + bank_norm[None, :] - (np.float32(2.0) * (chunk @ bank_vectors.T))
        np.maximum(distances_sq, np.float32(0.0), out=distances_sq)
        if k == bank_vectors.shape[0]:
            nearest_sq = np.sort(distances_sq, axis=1)
        else:
            nearest_sq = np.partition(distances_sq, kth=k - 1, axis=1)[:, :k]
            nearest_sq.sort(axis=1)
        result[start:end] = np.sqrt(nearest_sq).astype(np.float32, copy=False)
    return result


def _nearest_finite_distances(distances_raw: np.ndarray) -> np.ndarray:
    distances = np.sqrt(np.maximum(np.asarray(distances_raw, dtype=np.float32), np.float32(0.0))).astype(
        np.float32,
        copy=False,
    )
    finite = np.isfinite(distances)
    valid_counts = finite.sum(axis=1)
    nearest = np.zeros(distances.shape[0], dtype=np.float32)
    if distances.shape[1] == 0:
        return nearest
    valid_rows = valid_counts > 0
    first_indices = np.argmax(finite, axis=1)
    nearest[valid_rows] = distances[np.arange(distances.shape[0])[valid_rows], first_indices[valid_rows]]
    return nearest


def _finite_row_distances(distances_raw: np.ndarray) -> np.ndarray:
    distances = np.sqrt(np.maximum(np.asarray(distances_raw, dtype=np.float32), np.float32(0.0))).astype(
        np.float32,
        copy=False,
    )
    return distances[np.isfinite(distances)]
