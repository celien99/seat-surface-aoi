from __future__ import annotations

import argparse
import json
import math
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
) -> dict[str, Any]:
    vectors = _load_vectors(input_path)
    selected = _coreset_stride(vectors, coreset_ratio)
    bank = {
        "version": version,
        "model_family": "patchcore",
        "embedding_dim": len(selected[0]),
        "coreset_ratio": coreset_ratio,
        "pca_version": pca_version,
        "faiss_enabled": faiss_enabled,
        "vectors": selected,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(bank, ensure_ascii=False, indent=2), encoding="utf-8")
    return bank


def _load_vectors(input_path: Path) -> list[list[float]]:
    vectors: list[list[float]] = []
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        raw = json.loads(line)
        vector = raw.get("embedding", raw) if isinstance(raw, dict) else raw
        if not isinstance(vector, list) or not vector:
            raise ValueError(f"{input_path}:{line_number}: embedding 必须是非空数组")
        parsed = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in parsed):
            raise ValueError(f"{input_path}:{line_number}: embedding 必须是有限数字")
        vectors.append(parsed)
    if not vectors:
        raise ValueError(f"没有读取到 embedding: {input_path}")
    dim = len(vectors[0])
    for index, vector in enumerate(vectors, start=1):
        if len(vector) != dim:
            raise ValueError(f"{input_path}:{index}: embedding 维度不一致 {len(vector)} != {dim}")
    return vectors


def _coreset_stride(vectors: list[list[float]], coreset_ratio: float) -> list[list[float]]:
    if coreset_ratio <= 0.0 or coreset_ratio > 1.0:
        raise ValueError("coreset_ratio 必须在 (0, 1] 范围内")
    keep_count = max(1, math.ceil(len(vectors) * coreset_ratio))
    if keep_count >= len(vectors):
        return vectors
    step = len(vectors) / keep_count
    selected: list[list[float]] = []
    seen: set[int] = set()
    for item in range(keep_count):
        index = min(int(item * step), len(vectors) - 1)
        while index in seen and index + 1 < len(vectors):
            index += 1
        seen.add(index)
        selected.append(vectors[index])
    return selected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建 PatchCore memory bank JSON")
    parser.add_argument("--input", required=True, type=Path, help="JSONL embedding 文件，每行是数组或包含 embedding 字段")
    parser.add_argument("--output", required=True, type=Path, help="输出 memory bank JSON")
    parser.add_argument("--version", required=True, help="memory bank 版本")
    parser.add_argument("--coreset-ratio", type=float, default=1.0, help="coreset 采样比例，范围 (0, 1]")
    parser.add_argument("--pca-version", default=None, help="可选 PCA 版本")
    parser.add_argument("--faiss-enabled", action="store_true", help="记录该 bank 可由 FAISS 索引加速")
    args = parser.parse_args(argv)
    bank = build_memory_bank(
        args.input,
        args.output,
        version=args.version,
        coreset_ratio=args.coreset_ratio,
        pca_version=args.pca_version,
        faiss_enabled=args.faiss_enabled,
    )
    print(
        f"memory_bank={args.output} vectors={len(bank['vectors'])} dim={bank['embedding_dim']} "
        f"version={bank['version']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

