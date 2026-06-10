from __future__ import annotations

import json
from pathlib import Path

import pytest

from training_tools.build_patchcore_memory_bank import build_memory_bank


@pytest.fixture
def embedding_jsonl(tmp_path: Path) -> Path:
    path = tmp_path / "embeddings.jsonl"
    entries = []
    for idx in range(30):
        embedding = [float((idx + i) % 7) for i in range(10)]
        entries.append(json.dumps({"sample_id": f"sample_{idx}", "embedding": embedding}))
    path.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return path


def test_greedy_coreset_default(tmp_path: Path, embedding_jsonl: Path) -> None:
    """默认 coreset_method=greedy 验证输出格式。"""
    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embedding_jsonl,
        output_path=output,
        version="test_v1",
        coreset_ratio=0.5,
        pca_version=None,
        faiss_enabled=False,
    )
    assert len(bank["vectors"]) == 15
    assert bank["embedding_dim"] == 10
    assert bank["version"] == "test_v1"
    assert bank["model_family"] == "patchcore"


def test_stride_coreset_fallback(tmp_path: Path, embedding_jsonl: Path) -> None:
    """coreset_method=stride 使用等步长采样。"""
    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embedding_jsonl,
        output_path=output,
        version="test_v1",
        coreset_ratio=0.5,
        pca_version=None,
        faiss_enabled=False,
        coreset_method="stride",
    )
    assert len(bank["vectors"]) == 15


def test_greedy_coreset_diverse(tmp_path: Path) -> None:
    """greedy coreset 应选出比 stride 更多样化的子集。"""
    embeddings = tmp_path / "embeddings.jsonl"
    entries = []
    for idx in range(50):
        embedding = [float(idx % 10) * 0.01 for _ in range(5)]
        entries.append(json.dumps({"sample_id": f"cluster_a_{idx}", "embedding": embedding}))
    for idx in range(50):
        embedding = [100.0 + float(idx % 10) * 0.01 for _ in range(5)]
        entries.append(json.dumps({"sample_id": f"cluster_b_{idx}", "embedding": embedding}))
    embeddings.write_text("\n".join(entries) + "\n", encoding="utf-8")

    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embeddings,
        output_path=output,
        version="test_v1",
        coreset_ratio=0.2,
        pca_version=None,
        faiss_enabled=False,
        coreset_method="greedy",
    )
    vectors = bank["vectors"]
    small_count = sum(1 for v in vectors if max(v) < 10.0)
    large_count = sum(1 for v in vectors if max(v) > 50.0)
    assert small_count > 0, "greedy coreset 应覆盖小值聚类"
    assert large_count > 0, "greedy coreset 应覆盖大值聚类"


def test_coreset_ratio_one_keeps_all(tmp_path: Path, embedding_jsonl: Path) -> None:
    """coreset_ratio=1.0 保留全部向量。"""
    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embedding_jsonl,
        output_path=output,
        version="test_v1",
        coreset_ratio=1.0,
        pca_version=None,
        faiss_enabled=False,
    )
    assert len(bank["vectors"]) == 30
