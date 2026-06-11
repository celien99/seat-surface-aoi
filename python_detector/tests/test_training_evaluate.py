from __future__ import annotations

import json
from pathlib import Path

from training_tools.evaluate_pipeline import compute_iou, evaluate_detections


def test_compute_iou_full_overlap() -> None:
    """完全重叠的 bbox IoU = 1.0。"""
    assert compute_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0


def test_compute_iou_no_overlap() -> None:
    """完全不重叠的 bbox IoU = 0.0。"""
    assert compute_iou((0, 0, 5, 5), (10, 10, 15, 15)) == 0.0


def test_compute_iou_partial() -> None:
    """部分重叠：两个 10x10 bbox 偏移 5px。"""
    iou = compute_iou((0, 0, 10, 10), (5, 5, 15, 15))
    assert 0.1 < iou < 0.2


def test_evaluate_detections_all_matched() -> None:
    """所有预测 bbox 正确匹配 ground truth。"""
    preds = [
        {"class_name": "scratch", "score": 0.9, "bbox_xyxy_pixel": (10, 10, 30, 30)},
        {"class_name": "dent", "score": 0.7, "bbox_xyxy_pixel": (50, 50, 80, 80)},
    ]
    gts = [
        {"class_name": "scratch", "bbox_xyxy_pixel": (10, 10, 30, 30)},
        {"class_name": "dent", "bbox_xyxy_pixel": (50, 50, 80, 80)},
    ]
    report = evaluate_detections(preds, gts, iou_threshold=0.5)
    assert report["true_positives"] == 2
    assert report["false_positives"] == 0
    assert report["false_negatives"] == 0
    assert report["precision"] == 1.0
    assert report["recall"] == 1.0


def test_evaluate_detections_no_predictions() -> None:
    """没有预测但有 ground truth → recall=0。"""
    preds: list[dict] = []
    gts = [{"class_name": "scratch", "bbox_xyxy_pixel": (10, 10, 30, 30)}]
    report = evaluate_detections(preds, gts, iou_threshold=0.5)
    assert report["true_positives"] == 0
    assert report["false_negatives"] == 1
    assert report["recall"] == 0.0


def test_evaluate_detections_false_positives() -> None:
    """只有预测没有 ground truth → precision=0。"""
    preds = [{"class_name": "scratch", "score": 0.9, "bbox_xyxy_pixel": (10, 10, 30, 30)}]
    gts: list[dict] = []
    report = evaluate_detections(preds, gts, iou_threshold=0.5)
    assert report["false_positives"] == 1
    assert report["precision"] == 0.0


def test_evaluate_detections_score_below_threshold() -> None:
    """低分预测不计入匹配。"""
    preds = [{"class_name": "scratch", "score": 0.1, "bbox_xyxy_pixel": (10, 10, 30, 30)}]
    gts = [{"class_name": "scratch", "bbox_xyxy_pixel": (10, 10, 30, 30)}]
    report = evaluate_detections(preds, gts, iou_threshold=0.5, score_threshold=0.5)
    assert report["true_positives"] == 0
    assert report["false_negatives"] == 1


def test_evaluate_end_to_end_json(tmp_path: Path) -> None:
    """端到端：JSON manifest 图像输入 → 当前配方模型评估报告 JSON 输出。"""
    from training_tools.evaluate_pipeline import evaluate_from_manifest

    manifest = tmp_path / "manifest.jsonl"
    entries = []
    for index, light_id in enumerate(("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")):
        image_path = Path("images/TOP_BACK/full") / light_id / f"sample_1_{light_id}.pgm"
        full_path = tmp_path / image_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        pixels = bytes(80 + index * 10 + ((x + y) % 9) for y in range(48) for x in range(64))
        full_path.write_bytes(b"P5\n64 48\n255\n" + pixels)
        entries.append({
            "sample_id": f"sample_1_{light_id}",
            "source_trace_dir": "trace/SIM_1",
            "recipe_id": "seat_a_black_leather_v1",
            "seat_id": "SIM_1",
            "sequence_id": 1,
            "decision": "NG",
            "quality_pass": True,
            "camera_id": "TOP_BACK",
            "roi_name": "full",
            "light_id": light_id,
            "image_path": image_path.as_posix(),
            "split": "test",
            "label_status": "verified",
            "ground_truth_bbox": [[10, 10, 30, 30]],
            "ground_truth_class": ["scratch"],
        })
    manifest.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

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
    assert "by_class" in report["breakdown"]
    assert report["image_metrics"][0]["lights"] == ["DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT", "POLAR_DIFFUSE"]


def test_collect_trace_dataset_filter_decision(tmp_path: Path) -> None:
    """验证 --filter-decision 参数可筛选指定决策的样本。"""
    from training_tools.collect_trace_dataset import collect_trace_dataset

    for decision, seq_id in [("OK", 1), ("NG", 2)]:
        trace_dir = tmp_path / "trace" / f"SIM_{seq_id}_{seq_id}"
        images_dir = trace_dir / "images" / "TOP_BACK" / "full"
        images_dir.mkdir(parents=True, exist_ok=True)
        (images_dir / "DIFFUSE.pgm").write_bytes(b"P5\n1 1\n255\n\x80")
        (trace_dir / "result.json").write_text(json.dumps({
            "sequence_id": seq_id,
            "seat_id": f"SIM_{seq_id}",
            "decision": decision,
            "quality_pass": decision == "OK",
            "defects": [] if decision == "OK" else [
                {"class_name": "scratch", "camera_id": "TOP_BACK", "roi_name": "full", "bbox_xyxy_pixel": [10, 10, 20, 20]}
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
