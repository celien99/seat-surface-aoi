from __future__ import annotations

import argparse
import json
from pathlib import Path

from training_tools.training_errors import TrainingDataError


def compute_iou(
    bbox_a: tuple[int, int, int, int],
    bbox_b: tuple[int, int, int, int],
) -> float:
    """计算两个 xyxy bbox 的 IoU。"""
    x0, y0, x1, y1 = bbox_a
    bx0, by0, bx1, by1 = bbox_b
    inter_x0 = max(x0, bx0)
    inter_y0 = max(y0, by0)
    inter_x1 = min(x1, bx1)
    inter_y1 = min(y1, by1)
    inter_w = max(inter_x1 - inter_x0 + 1, 0)
    inter_h = max(inter_y1 - inter_y0 + 1, 0)
    intersection = inter_w * inter_h
    area_a = (x1 - x0 + 1) * (y1 - y0 + 1)
    area_b = (bx1 - bx0 + 1) * (by1 - by0 + 1)
    union = area_a + area_b - intersection
    return 0.0 if union <= 0 else intersection / union


def evaluate_detections(
    predictions: list[dict],
    ground_truths: list[dict],
    *,
    iou_threshold: float = 0.5,
    score_threshold: float = 0.0,
) -> dict:
    """对单张图的检测结果计算 TP/FP/FN/precision/recall。"""
    preds = [p for p in predictions if p.get("score", 0.0) >= score_threshold]
    matched_gt: set[int] = set()
    true_positives = 0
    false_positives = 0

    for pred in preds:
        pred_bbox = tuple(int(v) for v in pred["bbox_xyxy_pixel"])
        best_iou = 0.0
        best_gt_idx = -1
        for idx, gt in enumerate(ground_truths):
            if idx in matched_gt:
                continue
            if pred.get("class_name") != gt.get("class_name"):
                continue
            gt_bbox = tuple(int(v) for v in gt["bbox_xyxy_pixel"])
            iou = compute_iou(pred_bbox, gt_bbox)
            if iou > best_iou:
                best_iou = iou
                best_gt_idx = idx
        if best_iou >= iou_threshold and best_gt_idx >= 0:
            true_positives += 1
            matched_gt.add(best_gt_idx)
        else:
            false_positives += 1

    false_negatives = len(ground_truths) - len(matched_gt)
    total_preds = true_positives + false_positives
    total_gts = true_positives + false_negatives
    precision = true_positives / total_preds if total_preds > 0 else 1.0
    recall = true_positives / total_gts if total_gts > 0 else 1.0

    return {
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "precision": precision,
        "recall": recall,
    }


def evaluate_from_manifest(
    manifest_path: Path,
    output_path: Path,
    *,
    recipe_id: str = "seat_a_black_leather_v1",
    iou_threshold: float = 0.5,
    score_threshold: float = 0.0,
) -> dict:
    """从带标注的 manifest 评估检测流水线。"""
    if not manifest_path.exists():
        raise TrainingDataError(f"manifest 不存在: {manifest_path}")

    from python_detector.config.recipe_schema import RecipeManager
    from python_detector.pipeline.pipeline import InspectionPipeline
    from training_tools.job_fixture import make_simulated_job

    recipe = RecipeManager().load(recipe_id)
    pipeline = InspectionPipeline()

    total_tp = 0
    total_fp = 0
    total_fn = 0
    image_metrics: list[dict] = []

    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        entry = json.loads(line)
        gt_bboxes = entry.get("ground_truth_bbox", [])
        gt_classes = entry.get("ground_truth_class", [])
        gts = [
            {"class_name": cls, "bbox_xyxy_pixel": tuple(bbox)}
            for bbox, cls in zip(gt_bboxes, gt_classes)
        ]

        job = make_simulated_job(line_number)
        result = pipeline.process(job, recipe)

        preds = [
            {
                "class_name": defect.class_name,
                "score": defect.score,
                "bbox_xyxy_pixel": defect.bbox_xyxy_pixel,
            }
            for defect in result.defects
        ]

        metrics = evaluate_detections(preds, gts, iou_threshold=iou_threshold, score_threshold=score_threshold)
        total_tp += metrics["true_positives"]
        total_fp += metrics["false_positives"]
        total_fn += metrics["false_negatives"]
        image_metrics.append({
            "line": line_number,
            "sample_id": entry.get("sample_id", f"line_{line_number}"),
            "decision": result.decision,
            "quality_pass": result.quality_pass,
            **metrics,
        })

    total_preds = total_tp + total_fp
    total_gts = total_tp + total_fn
    overall = {
        "total_samples": len(image_metrics),
        "total_true_positives": total_tp,
        "total_false_positives": total_fp,
        "total_false_negatives": total_fn,
        "overall_precision": total_tp / total_preds if total_preds > 0 else 1.0,
        "overall_recall": total_tp / total_gts if total_gts > 0 else 1.0,
        "score_threshold": score_threshold,
        "iou_threshold": iou_threshold,
    }
    report = {"overall": overall, "image_metrics": image_metrics}

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评估检测流水线精度")
    parser.add_argument("--manifest", required=True, type=Path, help="带标注的 dataset manifest")
    parser.add_argument("--recipe", default="seat_a_black_leather_v1", help="配方 ID")
    parser.add_argument("--output", required=True, type=Path, help="评估报告 JSON 输出路径")
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument("--score-threshold", type=float, default=0.0)
    args = parser.parse_args(argv)

    try:
        report = evaluate_from_manifest(
            manifest_path=args.manifest,
            output_path=args.output,
            recipe_id=args.recipe,
            iou_threshold=args.iou_threshold,
            score_threshold=args.score_threshold,
        )
    except TrainingDataError as exc:
        print(f"evaluate_failed={exc}")
        return 2

    overall = report["overall"]
    print(
        f"samples={overall['total_samples']} "
        f"precision={overall['overall_precision']:.3f} "
        f"recall={overall['overall_recall']:.3f} "
        f"report={args.output}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
