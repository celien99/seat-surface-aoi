from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from python_detector.image_codec import write_gray_png
from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.train_patchcore_assets import train_patchcore_assets


@pytest.fixture
def embedding_npy(tmp_path: Path) -> Path:
    path = tmp_path / "embeddings.npy"
    matrix = np.asarray(
        [[float((idx + index) % 7) for index in range(10)] for idx in range(30)],
        dtype=np.float32,
    )
    np.save(path, matrix)
    return path


def test_greedy_coreset_default(tmp_path: Path, embedding_npy: Path) -> None:
    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embedding_npy,
        output_path=output,
        version="test_v1",
        coreset_ratio=0.5,
        pca_version=None,
        faiss_enabled=False,
    )
    assert bank["vector_count"] == 15
    assert bank["embedding_dim"] == 10
    assert bank["version"] == "test_v1"
    assert bank["model_family"] == "patchcore"
    assert (tmp_path / bank["vectors_path"]).exists()


def test_stride_coreset_fallback(tmp_path: Path, embedding_npy: Path) -> None:
    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embedding_npy,
        output_path=output,
        version="test_v1",
        coreset_ratio=0.5,
        pca_version=None,
        faiss_enabled=False,
        coreset_method="stride",
    )
    assert bank["vector_count"] == 15


def test_coreset_ratio_one_keeps_all(tmp_path: Path, embedding_npy: Path) -> None:
    output = tmp_path / "bank.json"
    bank = build_memory_bank(
        input_path=embedding_npy,
        output_path=output,
        version="test_v1",
        coreset_ratio=1.0,
        pca_version=None,
        faiss_enabled=False,
    )
    assert bank["vector_count"] == 30


def test_train_patchcore_assets_from_manifest(tmp_path: Path) -> None:
    manifest = _write_ok_manifest(tmp_path, count=3)
    output_dir = tmp_path / "patchcore"

    summary = train_patchcore_assets(
        manifest_path=manifest,
        output_dir=output_dir,
        embedding_backend="statistical",
        embedding_dim=10,
        split="train",
        pca_components=3,
        coreset_ratio=1.0,
        coreset_method="stride",
        build_faiss=False,
    )

    assert summary["embedding_count"] == 3
    assert summary["pca_output_dim"] == 3
    assert summary["memory_bank_vectors"] == 3
    assert summary["embeddings_npy_path"] is None
    assert summary["pca_embeddings_npy_path"] is None
    assert summary["intermediate_embeddings_retained"] is False
    assert summary["input_shape_summary"]["fixed_input_size"] is True
    assert summary["input_shape_summary"]["distinct_shapes"] == [
        {"input_shape_nchw": [1, 3, 48, 64], "count": 3}
    ]
    assert not (output_dir / "embeddings.npy").exists()
    assert not (output_dir / "pca_embeddings.npy").exists()
    assert (output_dir / "seat_pca.json").exists()
    assert (output_dir / "seat_patchcore_bank.json").exists()
    assert (output_dir / "seat_patchcore_bank.npy").exists()
    assert (output_dir / "patchcore_training_summary.json").exists()


def _write_ok_manifest(tmp_path: Path, count: int) -> Path:
    manifest = tmp_path / "manifest.jsonl"
    rows = []
    for sample_index in range(count):
        for light_index, light_id in enumerate(("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")):
            sample_id = f"ok_{sample_index}_{light_id}"
            image_path = Path("images/TOP_BACK/seat") / light_id / f"{sample_id}.png"
            full_path = tmp_path / image_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            pixels = bytes(
                50 + sample_index * 20 + light_index * 8 + ((x * 2 + y) % 17)
                for y in range(48)
                for x in range(64)
            )
            write_gray_png(full_path, 64, 48, pixels)
            rows.append(json.dumps({
                "sample_id": sample_id,
                "source_trace_dir": f"trace/SIM_{sample_index}",
                "recipe_id": "seat_a_black_leather_v1",
                "seat_id": f"SIM_{sample_index}",
                "sequence_id": sample_index + 1,
                "decision": "OK",
                "quality_pass": True,
                "camera_id": "TOP_BACK",
                "roi_name": "seat",
                "light_id": light_id,
                "image_path": image_path.as_posix(),
                "split": "train",
                "label_status": "verified_ok",
            }))
    manifest.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return manifest
