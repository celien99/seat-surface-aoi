from __future__ import annotations

import json
from pathlib import Path

import pytest

from python_detector.image_codec import write_gray_png
from training_tools.dataset_manifest import load_manifest_groups, read_pgm
from training_tools.extract_embeddings import extract_embeddings
from training_tools.training_errors import TrainingDataError


@pytest.fixture
def sample_manifest(tmp_path: Path) -> Path:
    manifest_path = tmp_path / "manifest.jsonl"
    rows = []
    for index, light_id in enumerate(("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")):
        image_path = Path("images/TOP_BACK/seat") / light_id / f"ok_1_{light_id}.png"
        full_path = tmp_path / image_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        pixels = bytes(60 + index * 20 + ((x + y) % 13) for y in range(48) for x in range(64))
        write_gray_png(full_path, 64, 48, pixels)
        rows.append(json.dumps({
            "sample_id": f"ok_1_{light_id}",
            "source_trace_dir": "trace/SIM_1",
            "recipe_id": "seat_a_black_leather_v1",
            "seat_id": "SIM_1",
            "sequence_id": 1,
            "camera_id": "TOP_BACK",
            "roi_name": "seat",
            "light_id": light_id,
            "decision": "OK",
            "quality_pass": True,
            "image_path": image_path.as_posix(),
            "split": "train",
            "label_status": "verified_ok",
        }))
    manifest_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest_path


def test_manifest_groups_load_png_images(tmp_path: Path, sample_manifest: Path) -> None:
    groups = load_manifest_groups(sample_manifest)
    assert len(groups) == 1
    assert groups[0].lights == ("DIFFUSE", "HIGH_LEFT", "POLAR_DIFFUSE")
    image = read_pgm(tmp_path / groups[0].rows[0].image_path)
    assert image.width == 64
    assert image.height == 48


def test_extract_embeddings_with_statistical_fallback(tmp_path: Path, sample_manifest: Path) -> None:
    output = tmp_path / "embeddings.jsonl"

    extract_embeddings(
        manifest_path=sample_manifest,
        output=output,
        embedding_dim=10,
        backend="statistical",
    )

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    for line in lines:
        entry = json.loads(line)
        assert entry["sample_id"] == "ok_1"
        assert "embedding" in entry
        assert len(entry["embedding"]) == 10
        assert all(isinstance(value, float) for value in entry["embedding"])
        assert entry["embedding"][0] > 0.0
        assert entry["input_shape_nchw"] == [1, 3, 48, 64]


def test_extract_embeddings_empty_manifest(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(TrainingDataError, match="没有 OK 样本"):
        extract_embeddings(manifest_path=empty, output=tmp_path / "out.jsonl", embedding_dim=10, backend="statistical")


def test_extract_embeddings_no_ok_samples(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps({"sample_id": "ng_1", "decision": "NG", "quality_pass": False, "camera_id": "TOP_BACK", "roi_name": "seat", "light_id": "DIFFUSE", "image_path": "x.png"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(TrainingDataError, match="没有 OK 样本"):
        extract_embeddings(manifest_path=manifest, output=tmp_path / "out.jsonl", embedding_dim=10, backend="statistical")
