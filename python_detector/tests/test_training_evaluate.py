from __future__ import annotations

import json
from pathlib import Path

from python_detector.image_codec import write_gray_png
from training_tools.evaluate_pipeline import compute_iou, evaluate_detections


def test_compute_iou_full_overlap() -> None:
    assert compute_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_compute_iou_no_overlap() -> None:
    assert compute_iou((0, 0, 5, 5), (10, 10, 15, 15)) == 0.0


def test_compute_iou_partial() -> None:
    iou = compute_iou((0, 0, 10, 10), (5, 5, 15, 15))
    assert 0.1 < iou < 0.2


def test_evaluate_detections_all_matched() -> None:
    preds = [
        {"score": 0.9, "bbox_xyxy_pixel": (10, 10, 30, 30)},
        {"score": 0.7, "bbox_xyxy_pixel": (50, 50, 80, 80)},
    ]
    gts = [
        {"bbox_xyxy_pixel": (10, 10, 30, 30)},
        {"bbox_xyxy_pixel": (50, 50, 80, 80)},
    ]
    report = evaluate_detections(preds, gts, iou_threshold=0.5)
    assert report["true_positives"] == 2
    assert report["false_positives"] == 0
    assert report["false_negatives"] == 0
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0


def test_evaluate_detections_no_predictions() -> None:
    preds: list[dict] = []
    gts = [{"bbox_xyxy_pixel": (10, 10, 30, 30)}]
    report = evaluate_detections(preds, gts, iou_threshold=0.5)
    assert report["true_positives"] == 0
    assert report["false_negatives"] == 1
    assert report["recall"] == 0.0


def test_evaluate_detections_false_positives() -> None:
    preds = [{"score": 0.9, "bbox_xyxy_pixel": (10, 10, 30, 30)}]
    gts: list[dict] = []
    report = evaluate_detections(preds, gts, iou_threshold=0.5)
    assert report["false_positives"] == 1
    assert report["precision"] == 0.0


def test_evaluate_detections_score_below_threshold() -> None:
    preds = [{"score": 0.1, "bbox_xyxy_pixel": (10, 10, 30, 30)}]
    gts = [{"bbox_xyxy_pixel": (10, 10, 30, 30)}]
    report = evaluate_detections(preds, gts, iou_threshold=0.5, score_threshold=0.5)
    assert report["true_positives"] == 0
    assert report["false_negatives"] == 1


def test_evaluate_end_to_end_json(tmp_path: Path) -> None:
    from training_tools.evaluate_pipeline import evaluate_from_manifest

    manifest = tmp_path / "manifest.jsonl"
    entries = []
    for index, light_id in enumerate(("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")):
        image_path = Path("images/TOP_BACK/seat") / light_id / f"sample_1_{light_id}.png"
        full_path = tmp_path / image_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        pixels = bytes(80 + index * 10 + ((x + y) % 9) for y in range(48) for x in range(64))
        write_gray_png(full_path, 64, 48, pixels)
        entries.append({
            "sample_id": f"sample_1_{light_id}",
            "source_trace_dir": "trace/SIM_1",
            "recipe_id": "seat_a_black_leather_v1",
            "seat_id": "SIM_1",
            "sequence_id": 1,
            "decision": "NG",
            "quality_pass": True,
            "camera_id": "TOP_BACK",
            "roi_name": "seat",
            "light_id": light_id,
            "image_path": image_path.as_posix(),
            "split": "test",
            "label_status": "verified",
            "ground_truth_bbox": [[10, 10, 30, 30]],
                    })
    manifest.write_text("\n".join(json.dumps(entry) for entry in entries) + "\n", encoding="utf-8")

    output = tmp_path / "evaluation_report.json"
    report = evaluate_from_manifest(
        manifest_path=manifest,
        output_path=output,
        recipe_id="seat_a_black_leather_v1",
        iou_threshold=0.5,
        split="test",
    )
    assert output.exists()
    assert "image_metrics" in report
    assert report["overall"]["total_samples"] == 1
    assert "by_roi" in report["breakdown"]
    assert report["image_metrics"][0]["lights"] == ["DIFFUSE", "HIGH_LEFT", "POLAR_DIFFUSE"]


def test_collect_trace_dataset_filter_decision(tmp_path: Path) -> None:
    from training_tools.collect_trace_dataset import collect_trace_dataset

    for decision, seq_id in [("OK", 1), ("NG", 2)]:
        trace_dir = tmp_path / "trace" / f"SIM_{seq_id}_{seq_id}"
        raw_dir = trace_dir / "raw_images"
        raw_dir.mkdir(parents=True, exist_ok=True)
        write_gray_png(raw_dir / "TOP_BACK_DIFFUSE.png", 1, 1, b"\x80")
        (trace_dir / "result.json").write_text(json.dumps({
            "sequence_id": seq_id,
            "seat_id": f"SIM_{seq_id}",
            "decision": decision,
            "quality_pass": decision == "OK",
            "defects": [] if decision == "OK" else [
                {"camera_id": "TOP_BACK", "roi_name": "seat", "bbox_xyxy_pixel": [10, 10, 20, 20]}
            ],
        }), encoding="utf-8")

    output = tmp_path / "filtered_dataset"
    samples = collect_trace_dataset(
        trace_roots=[tmp_path / "trace"],
        output_dir=output,
        filter_decision="OK",
    )
    assert len(samples) == 1
    assert samples[0].decision == "OK"
