from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def memory_bank_json(tmp_path: Path) -> Path:
    """构造包含 20 条 8 维向量的 memory bank JSON。"""
    path = tmp_path / "bank.json"
    bank = {
        "version": "bank_v1",
        "model_family": "patchcore",
        "embedding_dim": 8,
        "coreset_ratio": 1.0,
        "pca_version": "pca_v1",
        "faiss_enabled": True,
        "vectors": [[float(i + j) for j in range(8)] for i in range(20)],
    }
    path.write_text(json.dumps(bank), encoding="utf-8")
    return path


def test_build_faiss_flat_l2(tmp_path: Path, memory_bank_json: Path) -> None:
    """FlatL2 索引构建和搜索验证。"""
    faiss_available = False
    try:
        import faiss  # type: ignore  # noqa: F401
        import numpy  # type: ignore  # noqa: F401
        faiss_available = True
    except Exception:
        pass

    if not faiss_available:
        pytest.skip("faiss-cpu 未安装")

    from training_tools.build_faiss_index import build_faiss_index

    output = tmp_path / "index.faiss"
    result = build_faiss_index(
        bank_path=memory_bank_json,
        output=output,
        index_type="FlatL2",
        nlist=4,
    )

    assert output.exists()
    assert output.stat().st_size > 0
    assert result["index_type"] == "FlatL2"
    assert result["vector_count"] == 20
    assert result["dimension"] == 8

    import faiss as _faiss  # type: ignore
    import numpy as np  # type: ignore

    idx = _faiss.read_index(str(output))
    query = np.asarray([[1.0] * 8], dtype=np.float32)
    distances, indices = idx.search(query, 3)
    assert distances.shape == (1, 3)
    assert indices.shape == (1, 3)
    assert all(d >= 0.0 for d in distances[0])


def test_build_faiss_ivf_flat(tmp_path: Path, memory_bank_json: Path) -> None:
    """IVFFlat 索引构建。"""
    faiss_available = False
    try:
        import faiss  # type: ignore  # noqa: F401
        import numpy  # type: ignore  # noqa: F401
        faiss_available = True
    except Exception:
        pass

    if not faiss_available:
        pytest.skip("faiss-cpu 未安装")

    from training_tools.build_faiss_index import build_faiss_index

    output = tmp_path / "index.faiss"
    result = build_faiss_index(
        bank_path=memory_bank_json,
        output=output,
        index_type="IVFFlat",
        nlist=4,
    )
    assert result["index_type"] == "IVFFlat"
    assert output.stat().st_size > 0


def test_build_faiss_empty_bank(tmp_path: Path) -> None:
    """空 memory bank 抛出错误。"""
    faiss_available = False
    try:
        import faiss  # type: ignore  # noqa: F401
        faiss_available = True
    except Exception:
        pass
    if not faiss_available:
        pytest.skip("faiss-cpu 未安装")

    from training_tools.build_faiss_index import build_faiss_index
    from training_tools.training_errors import EmptyMemoryBankError

    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"version": "v", "model_family": "patchcore", "embedding_dim": 3, "vectors": []}), encoding="utf-8")

    with pytest.raises(EmptyMemoryBankError, match="为空"):
        build_faiss_index(bank_path=empty, output=tmp_path / "out.faiss")


def test_build_faiss_not_installed(tmp_path: Path, memory_bank_json: Path) -> None:
    """FAISS 未安装时优雅报错。"""
    faiss_available = False
    try:
        import faiss  # type: ignore  # noqa: F401
        faiss_available = True
    except Exception:
        pass

    if faiss_available:
        pytest.skip("faiss-cpu 已安装，跳过此测试")

    from training_tools.build_faiss_index import build_faiss_index
    from training_tools.training_errors import EmbeddingExtractionError

    with pytest.raises(EmbeddingExtractionError, match="faiss-cpu 未安装"):
        build_faiss_index(bank_path=memory_bank_json, output=tmp_path / "out.faiss")
