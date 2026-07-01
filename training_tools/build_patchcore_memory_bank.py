from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any


DEFAULT_RECHECK_QUANTILE = 0.99
DEFAULT_NG_QUANTILE = 0.999


def build_memory_bank(
    input_path: Path,
    output_path: Path,
    *,
    version: str,
    coreset_ratio: float,
    pca_version: str | None,
    faiss_enabled: bool,
    coreset_method: str = "greedy",
    metadata: dict[str, Any] | None = None,
    vectors_path: Path | None = None,
) -> dict[str, Any]:
    import numpy as np

    if coreset_method not in ("greedy", "stride"):
        raise ValueError(f"coreset_method 必须是 greedy 或 stride: {coreset_method}")
    vectors = _load_vector_matrix(input_path)
    if coreset_method == "greedy":
        selected = _coreset_greedy(vectors, coreset_ratio)
    else:
        selected = _coreset_stride(vectors, coreset_ratio)
    selected = np.asarray(selected, dtype=np.float32)
    if selected.ndim != 2 or selected.shape[0] <= 0 or selected.shape[1] <= 0:
        raise ValueError("PatchCore coreset 向量必须是非空二维矩阵")
    output_vectors_path = vectors_path or output_path.with_suffix(".npy")
    output_vectors_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(output_vectors_path, selected)
    bank = {
        "version": version,
        "model_family": "patchcore",
        "embedding_dim": int(selected.shape[1]),
        "coreset_ratio": coreset_ratio,
        "pca_version": pca_version,
        "faiss_enabled": faiss_enabled,
        "vector_count": int(selected.shape[0]),
        "vectors_path": _json_path(output_path, output_vectors_path),
    }
    if metadata is not None:
        bank["metadata"] = metadata
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    return bank


def _load_vector_matrix(input_path: Path):
    import numpy as np

    if not input_path.exists():
        raise ValueError(f"embedding 文件不存在: {input_path}")
    if input_path.suffix != ".npy":
        raise ValueError(f"PatchCore memory bank 构建只接受 .npy embedding 矩阵: {input_path}")
    vectors = np.load(str(input_path), mmap_mode="r", allow_pickle=False)
    return _validate_matrix(input_path, vectors)


def _validate_matrix(input_path: Path, vectors):
    import numpy as np

    if vectors.ndim != 2:
        raise ValueError(f"{input_path}: embedding 必须是二维矩阵")
    if vectors.shape[0] <= 0:
        raise ValueError(f"没有读取到 embedding: {input_path}")
    if vectors.shape[1] <= 0:
        raise ValueError(f"{input_path}: embedding 维度必须大于 0")
    if not bool(np.isfinite(vectors).all()):
        raise ValueError(f"{input_path}: embedding 必须是有限数字")
    if vectors.dtype != np.float32:
        return np.asarray(vectors, dtype=np.float32)
    return vectors


def _coreset_stride(vectors, coreset_ratio: float):
    import numpy as np

    if coreset_ratio <= 0.0 or coreset_ratio > 1.0:
        raise ValueError("coreset_ratio 必须在 (0, 1] 范围内")
    keep_count = max(1, math.ceil(vectors.shape[0] * coreset_ratio))
    if keep_count >= vectors.shape[0]:
        return vectors
    step = vectors.shape[0] / keep_count
    indices = np.asarray([min(int(item * step), vectors.shape[0] - 1) for item in range(keep_count)], dtype=np.int64)
    return vectors[indices]


def _euclidean_sq(left: list[float], right: list[float]) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def _coreset_greedy(vectors, coreset_ratio: float):
    """贪心最远点采样（近似最小化最大距离）。"""
    if coreset_ratio <= 0.0 or coreset_ratio > 1.0:
        raise ValueError("coreset_ratio 必须在 (0, 1] 范围内")
    keep_count = max(1, math.ceil(vectors.shape[0] * coreset_ratio))
    if keep_count >= vectors.shape[0]:
        return vectors
    selected_indices = [0]
    remaining = set(range(1, vectors.shape[0]))
    while len(selected_indices) < keep_count:
        farthest_idx = max(
            remaining,
            key=lambda i: min(
                _euclidean_sq(vectors[i], vectors[s]) for s in selected_indices
            ),
        )
        selected_indices.append(farthest_idx)
        remaining.remove(farthest_idx)
    return vectors[selected_indices]


def _json_path(output_path: Path, vectors_path: Path) -> str:
    try:
        return os.path.relpath(vectors_path, start=output_path.parent)
    except ValueError:
        return str(vectors_path)


def compute_calibration_stats(
    vectors_npy: Path,
    bank_json: Path,
    faiss_index_path: Path | None = None,
    sample_count: int = 50000,
    chunk_size: int = 4096,
    recheck_quantile: float = DEFAULT_RECHECK_QUANTILE,
    ng_quantile: float = DEFAULT_NG_QUANTILE,
) -> dict[str, float]:
    """计算 held-out 正常样本到 bank 的距离分布，写入校准统计量。

    将 bank 向量随机对半分：一半做"缩减 bank"，另一半做 held-out 查询集。
    held-out 向量不在缩减 bank 中，模拟了"测试图片不在训练集"的真实场景。
    用 held-out 查询缩减 bank 的最近邻距离分布作为 z-score 基准。
    """
    import numpy as np

    if not vectors_npy.exists():
        raise ValueError(f"bank vectors .npy 不存在: {vectors_npy}")
    if not 0.0 < recheck_quantile <= ng_quantile < 1.0:
        raise ValueError("阈值分位数必须满足 0 < recheck_quantile <= ng_quantile < 1")
    vectors = np.load(str(vectors_npy), mmap_mode="r", allow_pickle=False)
    if vectors.ndim != 2 or vectors.shape[0] <= 0:
        raise ValueError(f"bank vectors 必须是二维矩阵: {vectors.shape}")
    if vectors.shape[0] < 4:
        raise ValueError(f"bank 向量数不足 ({vectors.shape[0]})，无法做 held-out 校准")

    total = vectors.shape[0]
    rng = np.random.default_rng(42)
    # 随机对半分：一半做缩减 bank，一半做 held-out 查询
    perm = rng.permutation(total)
    split = total // 2
    reduced_indices = perm[:split]
    heldout_indices = perm[split : split * 2]

    reduced_vecs = np.asarray(vectors[reduced_indices], dtype=np.float32)
    heldout_queries = np.asarray(vectors[heldout_indices], dtype=np.float32)

    # held-out 采样
    heldout_sampled = min(sample_count, heldout_queries.shape[0])
    if heldout_sampled < heldout_queries.shape[0]:
        sample_idx = rng.choice(heldout_queries.shape[0], size=heldout_sampled, replace=False)
        heldout_queries = heldout_queries[sample_idx]

    # 在缩减 bank 上建临时 FAISS 索引
    faiss_available = False
    try:
        import faiss
        dim = int(reduced_vecs.shape[1])
        index = faiss.IndexFlatL2(dim)
        index.add(reduced_vecs)
        distances_raw, _ = index.search(heldout_queries.astype(np.float32, copy=False), 1)
        nearest_all = np.sqrt(np.maximum(distances_raw[:, 0].astype(np.float32), 0.0))
        faiss_available = True
    except Exception:
        # FAISS 不可用时用分块精确 KNN
        nearest_all = _heldout_knn_distances(heldout_queries, reduced_vecs, chunk_size=chunk_size)

    finite = np.isfinite(nearest_all)
    if not finite.any():
        raise ValueError("校准距离全为非有限值，无法计算统计量")

    distance_mean = float(np.mean(nearest_all[finite]))
    # 写回 bank JSON
    bank = {}
    if bank_json.exists() and bank_json.stat().st_size > 1:
        bank = json.loads(bank_json.read_text(encoding="utf-8"))
    # 空间 PatchCore 每张图有 H×W 个 patch（当前生产: 128×128=16384）。
    # 需要对正常样本做 bootstrap 模拟，计算每张正常图的"最异常 patch 距离"的期望值，
    # 以消除多重比较偏差。详见 patchcore.py _calibrated_score 注释。
    metadata = bank.get("metadata", {}) if isinstance(bank, dict) else {}
    spatial_h = int(metadata.get("spatial_upsample_height", 0))
    spatial_w = int(metadata.get("spatial_upsample_width", 0))
    _patch_grid = max(spatial_h * spatial_w, 1) if spatial_h > 0 and spatial_w > 0 else (128 * 128)
    _n_bootstrap = min(500, int(heldout_sampled))
    _maxima = np.empty(_n_bootstrap, dtype=np.float32)
    _rng = np.random.default_rng(42)
    for _i in range(_n_bootstrap):
        _sample = _rng.choice(nearest_all[finite], size=_patch_grid, replace=True)
        _maxima[_i] = np.max(_sample)
    distance_p99 = float(np.mean(_maxima))
    distance_std = float(np.std(_maxima))  # 保留仅供诊断
    maxima_scores = _calibrated_scores(_maxima, distance_mean, distance_p99)
    recheck_score = float(np.quantile(maxima_scores, recheck_quantile))
    ng_score = float(np.quantile(maxima_scores, ng_quantile))
    # 避免浮点抖动导致 NG 低于 RECHECK；阈值仍来自正常样本 bootstrap 分布。
    ng_score = max(ng_score, recheck_score)

    bank["distance_mean"] = distance_mean
    bank["distance_p99"] = distance_p99
    bank["thresholds"] = {
        "source": "normal_bootstrap_quantile",
        "recheck_score": recheck_score,
        "ng_score": ng_score,
        "normal_quantile_recheck": recheck_quantile,
        "normal_quantile_ng": ng_quantile,
        "bootstrap_image_count": int(_n_bootstrap),
        "patches_per_image": int(_patch_grid),
    }
    bank_json.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"calibration_stats: heldout={heldout_sampled}/{heldout_queries.shape[0]} "
        f"reduced_bank={reduced_vecs.shape[0]} "
        f"mean={distance_mean:.4f} p99={distance_p99:.4f} "
        f"recheck={recheck_score:.4f}@q{recheck_quantile:g} "
        f"ng={ng_score:.4f}@q{ng_quantile:g} "
        f"faiss={'yes' if faiss_available else 'no'} "
        f"written_to={bank_json}"
    )
    return {
        "distance_mean": distance_mean,
        "distance_p99": distance_p99,
        "recheck_score": recheck_score,
        "ng_score": ng_score,
    }


def _heldout_knn_distances(queries: "np.ndarray", bank_vecs: "np.ndarray", chunk_size: int = 4096) -> "np.ndarray":
    """分块精确 KNN：queries 对 bank_vecs 做最近邻搜索（queries 不在 bank 中）。"""
    import numpy as np

    result = np.empty(queries.shape[0], dtype=np.float32)
    bank_norm = np.sum(np.square(bank_vecs, dtype=np.float32), axis=1)
    for start in range(0, queries.shape[0], chunk_size):
        end = min(start + chunk_size, queries.shape[0])
        chunk = queries[start:end]
        query_norm = np.sum(np.square(chunk, dtype=np.float32), axis=1, keepdims=True)
        dist_sq = query_norm + bank_norm[None, :] - np.float32(2.0) * (chunk @ bank_vecs.T)
        np.maximum(dist_sq, np.float32(0.0), out=dist_sq)
        nearest_sq = np.min(dist_sq, axis=1)
        result[start:end] = np.sqrt(nearest_sq)
    return result


def _calibrated_scores(nearest, distance_mean: float, distance_p99: float):
    import numpy as np

    span = 2.0 * max(distance_p99 - distance_mean, 1e-6)
    return np.clip(np.tanh((nearest - distance_mean) / span), 0.0, 1.0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 PatchCore memory bank 元数据及向量资产，支持自校准统计")
    sub = parser.add_subparsers(dest="command")

    build_parser = sub.add_parser("build", help="从 .npy embedding 构建 memory bank")
    build_parser.add_argument("--input", required=True, type=Path, help=".npy embedding 矩阵路径")
    build_parser.add_argument("--output", required=True, type=Path, help="输出 memory bank 元数据 JSON")
    build_parser.add_argument("--version", required=True, help="memory bank 版本")
    build_parser.add_argument("--coreset-ratio", type=float, default=1.0, help="coreset 采样比例，范围 (0, 1]")
    build_parser.add_argument("--coreset-method", default="greedy", choices=["greedy", "stride"],
                              help="coreset 采样方法，默认 greedy")
    build_parser.add_argument("--pca-version", default=None, help="可选 PCA 版本")
    build_parser.add_argument("--faiss-enabled", action="store_true", help="记录该 bank 可由 FAISS 索引加速")
    build_parser.add_argument("--vectors-output", type=Path, default=None, help="可选：PatchCore 向量 .npy 输出路径")

    calib_parser = sub.add_parser("calibrate", help="对已有 memory bank 做自校准，计算距离分布统计量")
    calib_parser.add_argument("--vectors", required=True, type=Path, help="bank vectors .npy 路径")
    calib_parser.add_argument("--bank", required=True, type=Path, help="memory bank JSON 路径（原位更新）")
    calib_parser.add_argument("--faiss-index", type=Path, default=None, help="FAISS 索引路径（可选，加速计算）")
    calib_parser.add_argument("--sample-count", type=int, default=50000, help="自校准采样数，默认 50000")
    calib_parser.add_argument("--recheck-quantile", type=float, default=DEFAULT_RECHECK_QUANTILE,
                              help="正常 bootstrap 最大异常分布的 RECHECK 分位数，默认 0.99")
    calib_parser.add_argument("--ng-quantile", type=float, default=DEFAULT_NG_QUANTILE,
                              help="正常 bootstrap 最大异常分布的 NG 分位数，默认 0.999")

    args = parser.parse_args(argv)
    if args.command == "calibrate":
        stats = compute_calibration_stats(
            vectors_npy=args.vectors,
            bank_json=args.bank,
            faiss_index_path=args.faiss_index,
            sample_count=args.sample_count,
            recheck_quantile=args.recheck_quantile,
            ng_quantile=args.ng_quantile,
        )
        print(
            f"distance_mean={stats['distance_mean']:.4f} "
            f"distance_p99={stats['distance_p99']:.4f} "
            f"recheck_score={stats['recheck_score']:.4f} "
            f"ng_score={stats['ng_score']:.4f}"
        )
        return 0

    if args.command is None:
        parser.print_help()
        return 1

    bank = build_memory_bank(
        args.input,
        args.output,
        version=args.version,
        coreset_ratio=args.coreset_ratio,
        pca_version=args.pca_version,
        faiss_enabled=args.faiss_enabled,
        coreset_method=args.coreset_method,
        vectors_path=args.vectors_output,
    )
    print(
        f"memory_bank={args.output} vectors={bank['vector_count']} dim={bank['embedding_dim']} "
        f"version={bank['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
