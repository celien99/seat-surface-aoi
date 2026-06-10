from __future__ import annotations

import json
from pathlib import Path

import pytest

from training_tools.compute_pca import compute_pca
from training_tools.training_errors import DimensionMismatchError, TrainingDataError


@pytest.fixture
def embedding_jsonl(tmp_path: Path) -> Path:
    """构造包含 20 条 10 维随机 embedding 的 JSONL 文件。"""
    path = tmp_path / "embeddings.jsonl"
    entries = []
    for idx in range(20):
        embedding = [float(idx % 5) + float(i) * 0.1 for i in range(10)]
        entries.append(json.dumps({
            "sample_id": f"sample_{idx}",
            "camera_id": "TOP_BACK",
            "roi_name": "full",
            "embedding": embedding,
            "embedding_dim": 10,
        }))
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path


def test_compute_pca_output_format(tmp_path: Path, embedding_jsonl: Path) -> None:
    """验证 PCA 输出 JSON 的结构和维度。"""
    output = tmp_path / "pca.json"
    n_components = 5
    result = compute_pca(
        input_path=embedding_jsonl,
        output_path=output,
        n_components=n_components,
        version="test_v1",
    )
    assert result["version"] == "test_v1"
    assert result["input_dim"] == 10
    assert result["output_dim"] == n_components
    assert len(result["mean"]) == 10
    assert len(result["components"]) == n_components
    for component in result["components"]:
        assert len(component) == 10
    assert len(result["explained_variance_ratio"]) == n_components
    for ratio in result["explained_variance_ratio"]:
        assert 0.0 <= ratio <= 1.0

    assert output.exists()
    loaded = json.loads(output.read_text(encoding="utf-8"))
    assert loaded == result


def test_compute_pca_output_embeddings(tmp_path: Path, embedding_jsonl: Path) -> None:
    """验证 --output-embeddings 输出降维后的 embedding。"""
    output_pca = tmp_path / "pca.json"
    output_emb = tmp_path / "pca_embeddings.jsonl"

    compute_pca(
        input_path=embedding_jsonl,
        output_path=output_pca,
        n_components=3,
        version="test_v1",
        output_embeddings=output_emb,
    )

    assert output_emb.exists()
    lines = output_emb.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 20
    for line in lines:
        entry = json.loads(line)
        assert len(entry["embedding"]) == 3


def test_compute_pca_n_components_exceeds_input(tmp_path: Path, embedding_jsonl: Path) -> None:
    """目标维度超过输入维度时抛出错误。"""
    with pytest.raises(DimensionMismatchError, match="不能超过"):
        compute_pca(
            input_path=embedding_jsonl,
            output_path=tmp_path / "pca.json",
            n_components=20,
            version="test_v1",
        )


def test_compute_pca_empty_input(tmp_path: Path) -> None:
    """空 JSONL 抛出错误。"""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(TrainingDataError, match="没有读取到 embedding"):
        compute_pca(
            input_path=empty,
            output_path=tmp_path / "pca.json",
            n_components=5,
            version="test_v1",
        )


def test_compute_pca_dimension_mismatch(tmp_path: Path) -> None:
    """不同维度 embedding 混合时抛出错误。"""
    bad = tmp_path / "bad.jsonl"
    entries = [
        json.dumps({"sample_id": "a", "camera_id": "X", "roi_name": "r", "embedding": [1.0, 2.0, 3.0], "embedding_dim": 3}),
        json.dumps({"sample_id": "b", "camera_id": "X", "roi_name": "r", "embedding": [1.0, 2.0, 3.0, 4.0], "embedding_dim": 4}),
    ]
    bad.write_text("\n".join(entries) + "\n", encoding="utf-8")
    with pytest.raises(DimensionMismatchError, match="维度不一致"):
        compute_pca(
            input_path=bad,
            output_path=tmp_path / "pca.json",
            n_components=2,
            version="test_v1",
        )
