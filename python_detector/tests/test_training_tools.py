from __future__ import annotations

import json
from pathlib import Path

import pytest

from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.collect_trace_dataset import TraceDatasetError, collect_trace_dataset, main as collect_main
from tools.build_patchcore_memory_bank import build_memory_bank as compat_build_memory_bank


def test_collect_trace_dataset_generates_manifest_and_images(tmp_path: Path) -> None:
    trace_dir = _write_trace(tmp_path / "trace" / "20260609" / "SIM_1_1")
    output = tmp_path / "dataset"

    samples = collect_trace_dataset([tmp_path / "trace"], output, split="train")

    manifest_path = output / "dataset_manifest.jsonl"
    summary_path = output / "dataset_summary.json"
    assert len(samples) == 2
    assert manifest_path.exists()
    assert summary_path.exists()
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()]
    assert {row["light_id"] for row in rows} == {"DIFFUSE", "HIGH_LEFT"}
    assert rows[0]["source_trace_dir"] == str(trace_dir)
    assert rows[0]["recipe_id"] == "seat_a_black_leather_v1"
    assert rows[0]["seat_id"] == "SIM_1"
    assert rows[0]["sequence_id"] == 1
    assert rows[0]["decision"] == "NG"
    assert rows[0]["split"] == "train"
    assert rows[0]["label_status"] == "unlabeled"
    assert all(row["has_defect"] is True for row in rows)
    assert all(row["defect_classes"] == ["scratch"] for row in rows)
    assert all(row["bbox_xyxy_pixel"] == [[1, 2, 10, 12]] for row in rows)
    for row in rows:
        assert (output / row["image_path"]).read_bytes().startswith(b"P5\n")


def test_collect_trace_dataset_fails_on_empty_trace_root(tmp_path: Path) -> None:
    trace_root = tmp_path / "trace"
    trace_root.mkdir()

    with pytest.raises(TraceDatasetError, match="没有发现可用 trace 记录"):
        collect_trace_dataset([trace_root], tmp_path / "dataset")


def test_collect_trace_dataset_fails_on_missing_images(tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace" / "SIM_1_1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "result.json").write_text('{"sequence_id":1,"seat_id":"SIM_1","decision":"OK"}', encoding="utf-8")

    with pytest.raises(TraceDatasetError, match="缺少 ROI 图像目录"):
        collect_trace_dataset([trace_dir], tmp_path / "dataset")


def test_collect_trace_dataset_cli_reports_broken_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    trace_dir = tmp_path / "trace" / "SIM_1_1"
    trace_dir.mkdir(parents=True)
    (trace_dir / "result.json").write_text("{broken", encoding="utf-8")

    code = collect_main(["--trace-root", str(trace_dir), "--output", str(tmp_path / "dataset")])

    captured = capsys.readouterr()
    assert code == 2
    assert "collect_trace_dataset_failed=JSON 解析失败" in captured.out


def test_patchcore_memory_bank_builder_is_available_from_new_and_compat_modules(tmp_path: Path) -> None:
    embeddings = tmp_path / "embeddings.jsonl"
    embeddings.write_text(
        "\n".join(json.dumps({"embedding": [float(index), float(index + 1)]}) for index in range(4)),
        encoding="utf-8",
    )
    output = tmp_path / "bank.json"
    compat_output = tmp_path / "compat_bank.json"

    bank = build_memory_bank(
        embeddings,
        output,
        version="bank_v1",
        coreset_ratio=0.5,
        pca_version="pca_v1",
        faiss_enabled=True,
    )
    compat_bank = compat_build_memory_bank(
        embeddings,
        compat_output,
        version="bank_v1",
        coreset_ratio=0.5,
        pca_version="pca_v1",
        faiss_enabled=True,
    )

    assert bank == compat_bank
    assert bank["embedding_dim"] == 2
    assert len(bank["vectors"]) == 2


def _write_trace(trace_dir: Path) -> Path:
    image_dir = trace_dir / "images" / "TOP_BACK" / "full"
    image_dir.mkdir(parents=True)
    for light_id in ("DIFFUSE", "HIGH_LEFT"):
        (image_dir / f"{light_id}.pgm").write_bytes(b"P5\n2 2\n255\n\x01\x02\x03\x04")
    (trace_dir / "job.json").write_text(
        json.dumps(
            {
                "sequence_id": 1,
                "trigger_id": 1001,
                "seat_id": "SIM_1",
                "recipe_id": "seat_a_black_leather_v1",
                "sku": "seat_a_black_leather",
            }
        ),
        encoding="utf-8",
    )
    (trace_dir / "recipe_summary.json").write_text(
        json.dumps({"recipe_id": "seat_a_black_leather_v1", "sku": "seat_a_black_leather"}),
        encoding="utf-8",
    )
    (trace_dir / "result.json").write_text(
        json.dumps(
            {
                "sequence_id": 1,
                "trigger_id": 1001,
                "seat_id": "SIM_1",
                "decision": "NG",
                "quality_pass": True,
                "defects": [
                    {
                        "defect_id": "defect_1",
                        "class_name": "scratch",
                        "camera_id": "TOP_BACK",
                        "roi_name": "full",
                        "bbox_xyxy_pixel": [1, 2, 10, 12],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return trace_dir

