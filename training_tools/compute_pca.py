from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from training_tools.training_errors import DimensionMismatchError, TrainingDataError


def compute_pca(
    input_path: Path,
    output_path: Path,
    *,
    n_components: int = 256,
    version: str,
    output_embeddings: Path | None = None,
) -> dict:
    """用 numpy SVD 计算 PCA 参数并输出 JSON。"""
    vectors, _metadata = _load_vectors(input_path)
    dim = len(vectors[0])

    if n_components > dim:
        raise DimensionMismatchError(
            f"n_components ({n_components}) 不能超过输入维度 ({dim})"
        )

    import numpy as np

    matrix = np.asarray(vectors, dtype=np.float64)
    mean = np.mean(matrix, axis=0)
    centered = matrix - mean

    u, s, vt = np.linalg.svd(centered, full_matrices=False)
    components = vt[:n_components]
    total_variance = float(np.sum(s ** 2))
    explained_variance_ratio = [
        float((sv ** 2) / total_variance) if total_variance > 0 else 0.0
        for sv in s[:n_components]
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

    if output_embeddings is not None:
        projected = (centered @ components.T).tolist()
        output_embeddings.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for idx, (meta, proj) in enumerate(zip(_metadata, projected)):
            entry = {
                "sample_id": meta.get("sample_id", f"pca_{idx}"),
                "camera_id": meta.get("camera_id", ""),
                "roi_name": meta.get("roi_name", ""),
                "embedding": [float(v) for v in proj],
                "embedding_dim": n_components,
                "pca_version": version,
            }
            lines.append(json.dumps(entry, ensure_ascii=False))
        output_embeddings.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return output


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
