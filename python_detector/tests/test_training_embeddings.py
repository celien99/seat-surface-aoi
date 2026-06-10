from __future__ import annotations

import json
from pathlib import Path

import pytest

from training_tools.extract_embeddings import extract_embeddings
from training_tools.training_errors import EmbeddingExtractionError, TrainingDataError


@pytest.fixture
def sample_manifest(tmp_path: Path) -> Path:
    """构造一个模拟 manifest，包含两条 OK 样本记录。"""
    manifest_path = tmp_path / "manifest.jsonl"
    lines = [
        json.dumps({
            "sample_id": "ok_1",
            "camera_id": "TOP_BACK",
            "roi_name": "full",
            "light_id": "DIFFUSE",
            "decision": "OK",
            "quality_pass": True,
            "image_path": "images/TOP_BACK/full/DIFFUSE/ok_1.pgm",
        }),
        json.dumps({
            "sample_id": "ok_2",
            "camera_id": "TOP_BACK",
            "roi_name": "full",
            "light_id": "POLAR_DIFFUSE",
            "decision": "OK",
            "quality_pass": True,
            "image_path": "images/TOP_BACK/full/POLAR_DIFFUSE/ok_2.pgm",
        }),
    ]
    manifest_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest_path


def test_extract_embeddings_with_statistical_fallback(tmp_path: Path, sample_manifest: Path) -> None:
    """statistical 模式不依赖 ONNX 模型，验证 JSONL 输出格式。"""
    output = tmp_path / "embeddings.jsonl"

    result = extract_embeddings(
        manifest_path=sample_manifest,
        output=output,
        embedding_dim=10,
        backend="statistical",
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # 两个 sample 同属 TOP_BACK/full → 按 (camera_id, roi_name) 分组 → 1 条输出
    for line in lines:
        entry = json.loads(line)
        assert "sample_id" in entry
        assert "embedding" in entry
        assert len(entry["embedding"]) == 10
        assert all(isinstance(v, float) for v in entry["embedding"])


def test_extract_embeddings_empty_manifest(tmp_path: Path) -> None:
    """空 manifest 抛出 TrainingDataError。"""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(TrainingDataError, match="没有 OK 样本"):
        extract_embeddings(manifest_path=empty, output=tmp_path / "out.jsonl", embedding_dim=10, backend="statistical")


def test_extract_embeddings_no_ok_samples(tmp_path: Path) -> None:
    """manifest 中没有 OK 样本时抛出错误。"""
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({"sample_id": "ng_1", "decision": "NG", "quality_pass": False, "camera_id": "TOP_BACK", "roi_name": "full", "light_id": "DIFFUSE", "image_path": "x.pgm"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TrainingDataError, match="没有 OK 样本"):
        extract_embeddings(manifest_path=manifest, output=tmp_path / "out.jsonl", embedding_dim=10, backend="statistical")
