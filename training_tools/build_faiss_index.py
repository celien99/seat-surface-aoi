from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from training_tools.training_errors import EmbeddingExtractionError, EmptyMemoryBankError


def build_faiss_index(
    bank_path: Path,
    output: Path,
    *,
    index_type: str = "FlatL2",
    nlist: int | None = None,
) -> dict:
    """从 memory bank JSON 构建 FAISS 索引。"""
    if not bank_path.exists():
        raise EmbeddingExtractionError(f"memory bank 不存在: {bank_path}")
    raw = json.loads(bank_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise EmbeddingExtractionError(f"memory bank 必须是 JSON object: {bank_path}")
    vectors_raw = raw.get("vectors", [])
    if not isinstance(vectors_raw, list) or not vectors_raw:
        raise EmptyMemoryBankError(f"memory bank 为空: {bank_path}")
    dimension = int(raw.get("embedding_dim", 0))
    if dimension <= 0:
        raise EmbeddingExtractionError(f"memory bank embedding_dim 无效: {dimension}")

    try:
        import faiss  # type: ignore
        import numpy as np  # type: ignore
    except Exception as exc:
        raise EmbeddingExtractionError(
            "faiss-cpu 未安装，无法构建 FAISS 索引。安装: uv sync --group training"
        ) from exc

    vectors = np.asarray(vectors_raw, dtype=np.float32)
    if vectors.shape[1] != dimension:
        raise EmbeddingExtractionError(
            f"vector 维度不匹配: {vectors.shape[1]} != {dimension}"
        )

    if index_type == "FlatL2":
        index = faiss.IndexFlatL2(dimension)
    elif index_type == "IVFFlat":
        quantizer = faiss.IndexFlatL2(dimension)
        actual_nlist = nlist if nlist is not None else int(math.sqrt(len(vectors_raw)))
        actual_nlist = max(1, min(actual_nlist, len(vectors_raw)))
        index = faiss.IndexIVFFlat(quantizer, dimension, actual_nlist)
        index.train(vectors)
    else:
        raise EmbeddingExtractionError(f"不支持的 FAISS 索引类型: {index_type}")

    index.add(vectors)

    output.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(output))

    loaded = faiss.read_index(str(output))
    if loaded.d != dimension:
        raise EmbeddingExtractionError(f"FAISS 索引校验失败：维度不匹配 {loaded.d} != {dimension}")
    if loaded.ntotal != len(vectors_raw):
        raise EmbeddingExtractionError(f"FAISS 索引校验失败：向量数不匹配 {loaded.ntotal} != {len(vectors_raw)}")
    if index_type == "FlatL2":
        query = np.zeros((1, dimension), dtype=np.float32)
        distances, _indices = loaded.search(query, 1)
        if distances.shape != (1, 1):
            raise EmbeddingExtractionError("FAISS 索引校验失败：搜索返回形状不正确")

    return {
        "index_type": index_type,
        "vector_count": len(vectors_raw),
        "dimension": dimension,
        "faiss_index_path": str(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 memory bank JSON 构建 FAISS 索引")
    parser.add_argument("--bank", required=True, type=Path, help="memory bank JSON 路径")
    parser.add_argument("--output", required=True, type=Path, help="FAISS 索引输出路径")
    parser.add_argument("--index-type", default="FlatL2", choices=["FlatL2", "IVFFlat"])
    parser.add_argument("--nlist", type=int, default=None, help="IVF 聚类数")
    args = parser.parse_args(argv)

    try:
        result = build_faiss_index(
            bank_path=args.bank,
            output=args.output,
            index_type=args.index_type,
            nlist=args.nlist,
        )
    except (EmbeddingExtractionError, EmptyMemoryBankError) as exc:
        print(f"build_faiss_failed={exc}")
        return 2

    print(
        f"faiss_index={args.output} index_type={result['index_type']} "
        f"vectors={result['vector_count']} dim={result['dimension']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
