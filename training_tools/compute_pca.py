from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterator

from training_tools.training_errors import DimensionMismatchError, TrainingDataError


def compute_pca(
    input_path: Path,
    output_path: Path,
    *,
    n_components: int = 256,
    version: str,
    output_embeddings: Path | None = None,
) -> dict:
    """用批量协方差特征分解计算 PCA 参数并输出 JSON。"""
    import numpy as np

    count, dim, vector_sum = _scan_vector_sum(input_path)

    if n_components > dim:
        raise DimensionMismatchError(
            f"n_components ({n_components}) 不能超过输入维度 ({dim})"
        )
    if n_components > count:
        raise DimensionMismatchError(
            f"n_components ({n_components}) 不能超过样本数 ({count})"
        )

    mean = vector_sum / float(count)
    scatter = np.zeros((dim, dim), dtype=np.float64)
    for batch, _metadata in _iter_embedding_batches(input_path, expected_dim=dim):
        centered = batch - mean
        scatter += centered.T @ centered

    eigenvalues, eigenvectors = np.linalg.eigh(scatter)
    order = np.argsort(eigenvalues)[::-1]
    selected = order[:n_components]
    components = eigenvectors[:, selected].T
    selected_values = np.maximum(eigenvalues[selected], 0.0)
    total_variance = float(np.maximum(eigenvalues, 0.0).sum())
    explained_variance_ratio = [
        float(value / total_variance) if total_variance > 0 else 0.0
        for value in selected_values.tolist()
    ]

    output = {
        "version": version,
        "input_dim": dim,
        "output_dim": n_components,
        "mean": [float(v) for v in mean.tolist()],
        "components": [[float(v) for v in row.tolist()] for row in components],
        "explained_variance_ratio": explained_variance_ratio,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    if output_embeddings is not None and output_embeddings.suffix == ".npy":
        output_embeddings.parent.mkdir(parents=True, exist_ok=True)
        projected_matrix = np.lib.format.open_memmap(
            output_embeddings,
            mode="w+",
            dtype=np.float32,
            shape=(count, n_components),
        )
        written = 0
        for batch, _metadata in _iter_embedding_batches(input_path, expected_dim=dim):
            projected = (batch - mean) @ components.T
            end = written + int(projected.shape[0])
            projected_matrix[written:end, :] = projected.astype(np.float32, copy=False)
            written = end
        if written != count:
            raise TrainingDataError(f"PCA embedding 写入数量不匹配: {written} != {count}")
        projected_matrix.flush()
    elif output_embeddings is not None:
        output_embeddings.parent.mkdir(parents=True, exist_ok=True)
        with output_embeddings.open("w", encoding="utf-8") as handle:
            written = 0
            for batch, metadata in _iter_embedding_batches(input_path, expected_dim=dim):
                projected = (batch - mean) @ components.T
                for meta, proj in zip(metadata, projected):
                    entry = {
                        "sample_id": meta.get("sample_id", f"pca_{written}"),
                        "camera_id": meta.get("camera_id", ""),
                        "roi_name": meta.get("roi_name", ""),
                        "embedding": [float(v) for v in proj.tolist()],
                        "embedding_dim": n_components,
                        "pca_version": version,
                    }
                    handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                    written += 1

    return output


def _scan_vector_sum(input_path: Path) -> tuple[int, int, "np.ndarray"]:
    import numpy as np

    count = 0
    dim: int | None = None
    vector_sum: np.ndarray | None = None
    for batch, _metadata in _iter_embedding_batches(input_path):
        if dim is None:
            dim = int(batch.shape[1])
            vector_sum = np.zeros(dim, dtype=np.float64)
        count += int(batch.shape[0])
        vector_sum += batch.sum(axis=0)
    if count <= 0 or dim is None or vector_sum is None:
        raise TrainingDataError(f"没有读取到 embedding: {input_path}")
    return count, dim, vector_sum


def _iter_embedding_batches(
    input_path: Path,
    *,
    expected_dim: int | None = None,
    batch_size: int = 512,
) -> Iterator[tuple["np.ndarray", list[dict[str, Any]]]]:
    import numpy as np

    if not input_path.exists():
        raise TrainingDataError(f"embedding 文件不存在: {input_path}")
    if input_path.suffix == ".npy":
        yield from _iter_npy_embedding_batches(input_path, expected_dim=expected_dim, batch_size=batch_size)
        return
    vectors: list[list[float]] = []
    metadata: list[dict[str, Any]] = []
    dim = expected_dim
    saw_vector = False
    with input_path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            saw_vector = True
            try:
                entry = json.loads(line)
            except json.JSONDecodeError as exc:
                raise TrainingDataError(f"{input_path}:{line_number}: JSON 解析失败: {exc}") from exc
            if "embedding" not in entry:
                raise TrainingDataError(f"{input_path}:{line_number}: 缺少 embedding 字段")
            vector = [float(v) for v in entry["embedding"]]
            if not all(math.isfinite(v) for v in vector):
                raise TrainingDataError(f"{input_path}:{line_number}: embedding 必须是有限数字")
            if dim is None:
                dim = len(vector)
            elif len(vector) != dim:
                raise DimensionMismatchError(
                    f"{input_path}:{line_number}: embedding 维度不一致 {len(vector)} != {dim}"
                )
            vectors.append(vector)
            metadata.append(entry)
            if len(vectors) >= batch_size:
                yield np.asarray(vectors, dtype=np.float64), metadata
                vectors = []
                metadata = []
    if vectors:
        yield np.asarray(vectors, dtype=np.float64), metadata
    if not saw_vector:
        raise TrainingDataError(f"没有读取到 embedding: {input_path}")


def _iter_npy_embedding_batches(
    input_path: Path,
    *,
    expected_dim: int | None,
    batch_size: int,
) -> Iterator[tuple["np.ndarray", list[dict[str, Any]]]]:
    import numpy as np

    matrix = np.load(str(input_path), mmap_mode="r", allow_pickle=False)
    if matrix.ndim != 2:
        raise TrainingDataError(f"{input_path}: embedding .npy 必须是二维矩阵")
    if matrix.shape[0] <= 0:
        raise TrainingDataError(f"没有读取到 embedding: {input_path}")
    if matrix.shape[1] <= 0:
        raise TrainingDataError(f"{input_path}: embedding 维度必须大于 0")
    if expected_dim is not None and matrix.shape[1] != expected_dim:
        raise DimensionMismatchError(f"{input_path}: embedding 维度不一致 {matrix.shape[1]} != {expected_dim}")
    for start in range(0, int(matrix.shape[0]), batch_size):
        end = min(start + batch_size, int(matrix.shape[0]))
        batch = np.asarray(matrix[start:end], dtype=np.float64)
        if not bool(np.isfinite(batch).all()):
            raise TrainingDataError(f"{input_path}: embedding 必须是有限数字")
        metadata = [{"sample_id": f"embedding_{index}"} for index in range(start, end)]
        yield batch, metadata


def _load_vectors(input_path: Path) -> tuple[list[list[float]], list[dict]]:
    if not input_path.exists():
        raise TrainingDataError(f"embedding 文件不存在: {input_path}")
    vectors: list[list[float]] = []
    metadata: list[dict] = []
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingDataError(f"{input_path}:{line_number}: JSON 解析失败: {exc}") from exc
        if "embedding" not in entry:
            raise TrainingDataError(f"{input_path}:{line_number}: 缺少 embedding 字段")
        vector = [float(v) for v in entry["embedding"]]
        if not all(math.isfinite(v) for v in vector):
            raise TrainingDataError(f"{input_path}:{line_number}: embedding 必须是有限数字")
        vectors.append(vector)
        metadata.append(entry)
    if not vectors:
        raise TrainingDataError(f"没有读取到 embedding: {input_path}")
    dim = len(vectors[0])
    for idx, v in enumerate(vectors, start=1):
        if len(v) != dim:
            raise DimensionMismatchError(
                f"{input_path}:{idx}: embedding 维度不一致 {len(v)} != {dim}"
            )
    return vectors, metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 embedding JSONL 计算 PCA 参数")
    parser.add_argument("--input", required=True, type=Path, help="embedding JSONL 文件路径")
    parser.add_argument("--output-n-components", type=int, default=256, help="目标维度")
    parser.add_argument("--output", required=True, type=Path, help="PCA 参数 JSON 输出路径")
    parser.add_argument("--output-embeddings", type=Path, default=None, help="可选：同时输出降维后的 embedding JSONL")
    parser.add_argument("--version", required=True, help="PCA 版本号")
    args = parser.parse_args(argv)

    try:
        result = compute_pca(
            input_path=args.input,
            output_path=args.output,
            n_components=args.output_n_components,
            version=args.version,
            output_embeddings=args.output_embeddings,
        )
    except (TrainingDataError, DimensionMismatchError) as exc:
        print(f"compute_pca_failed={exc}")
        return 2

    print(
        f"pca={args.output} version={args.version} "
        f"input_dim={result['input_dim']} output_dim={result['output_dim']}"
    )
    if args.output_embeddings:
        print(f"pca_embeddings={args.output_embeddings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
