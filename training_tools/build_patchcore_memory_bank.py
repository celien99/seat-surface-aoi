from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
from typing import Any


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 .npy embedding 矩阵构建 PatchCore memory bank 元数据和向量资产")
    parser.add_argument("--input", required=True, type=Path, help=".npy embedding 矩阵路径")
    parser.add_argument("--output", required=True, type=Path, help="输出 memory bank 元数据 JSON")
    parser.add_argument("--version", required=True, help="memory bank 版本")
    parser.add_argument("--coreset-ratio", type=float, default=1.0, help="coreset 采样比例，范围 (0, 1]")
    parser.add_argument("--coreset-method", default="greedy", choices=["greedy", "stride"],
                        help="coreset 采样方法，默认 greedy")
    parser.add_argument("--pca-version", default=None, help="可选 PCA 版本")
    parser.add_argument("--faiss-enabled", action="store_true", help="记录该 bank 可由 FAISS 索引加速")
    parser.add_argument("--vectors-output", type=Path, default=None, help="可选：PatchCore 向量 .npy 输出路径")
    args = parser.parse_args(argv)
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
