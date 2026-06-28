from __future__ import annotations

import argparse
import json
from pathlib import Path

from python_detector.config.recipe_schema import Recipe, RecipeManager
from python_detector.models.inference_engine import InferenceEngine, ModelRegistry
from training_tools.dataset_manifest import (
    ManifestSampleGroup,
    build_feature_group_from_manifest_group,
    load_manifest_groups,
)
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


def evaluate_dataset_predictions(
    image_predictions: list[dict],
    *,
    iou_threshold: float = 0.5,
    score_threshold: float = 0.0,
) -> dict:
    total_tp = 0
    total_fp = 0
    total_fn = 0
    image_metrics: list[dict] = []
    breakdown: dict[str, dict[str, dict[str, int]]] = {
        "by_roi": {},
        "by_camera": {},
        "by_split": {},
    }

    for item in image_predictions:
        metrics = evaluate_detections(
            item["predictions"],
            item["ground_truths"],
            iou_threshold=iou_threshold,
            score_threshold=score_threshold,
        )
        total_tp += metrics["true_positives"]
        total_fp += metrics["false_positives"]
        total_fn += metrics["false_negatives"]
        image_metrics.append({**item["metadata"], **metrics})
        _accumulate_breakdown(breakdown["by_roi"], item["metadata"].get("roi_name", ""), metrics)
        _accumulate_breakdown(breakdown["by_camera"], item["metadata"].get("camera_id", ""), metrics)
        _accumulate_breakdown(breakdown["by_split"], item["metadata"].get("split", ""), metrics)

    total_preds = total_tp + total_fp
    total_gts = total_tp + total_fn
    return {
        "overall": {
            "total_samples": len(image_metrics),
            "total_true_positives": total_tp,
            "total_false_positives": total_fp,
            "total_false_negatives": total_fn,
            "overall_precision": total_tp / total_preds if total_preds > 0 else 1.0,
            "overall_recall": total_tp / total_gts if total_gts > 0 else 1.0,
            "score_threshold": score_threshold,
            "iou_threshold": iou_threshold,
        },
        "breakdown": _finalize_breakdown(breakdown),
        "image_metrics": image_metrics,
    }


def evaluate_from_manifest(
    manifest_path: Path,
    output_path: Path,
    *,
    recipe_id: str = "seat_a_black_leather_v1",
    iou_threshold: float = 0.5,
    score_threshold: float = 0.0,
    model_key: str | None = None,
    split: str | None = None,
) -> dict:
    """从带标注的 manifest 评估检测流水线。"""
    recipe = RecipeManager().load(recipe_id)
    groups = [group for group in load_manifest_groups(manifest_path) if split is None or group.split == split]
    if not groups:
        raise TrainingDataError(f"manifest 没有可评估样本: {manifest_path}")

    report = evaluate_dataset_predictions(
        _predict_manifest_groups(groups, recipe, model_key=model_key),
        iou_threshold=iou_threshold,
        score_threshold=score_threshold,
    )
    report["recipe_id"] = recipe_id
    report["model_key"] = model_key or "recipe_default"
    report["manifest"] = str(manifest_path)
    if split is not None:
        report["split"] = split

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

    return report


def _predict_manifest_groups(
    groups: list[ManifestSampleGroup],
    recipe: Recipe,
    *,
    model_key: str | None,
) -> list[dict]:
    engine = InferenceEngine(ModelRegistry())
    image_predictions: list[dict] = []
    for group in groups:
        selected_model_key = model_key or recipe.model_key_for(group.camera_id, group.roi_name, group.pose_id)
        if selected_model_key not in recipe.models:
            raise TrainingDataError(f"{group.group_id}: 配方缺少模型配置: {selected_model_key}")
        try:
            feature_group = build_feature_group_from_manifest_group(group, recipe, model_key=selected_model_key)
            candidates = engine.infer([feature_group], recipe)
        except Exception as exc:
            raise TrainingDataError(f"{group.group_id}: 检测评估失败: {exc}") from exc
        image_predictions.append(
            {
                "metadata": {
                    "group_id": group.group_id,
                    "sample_id": group.sample_id,
                    "source_trace_dir": group.source_trace_dir,
                    "camera_id": group.camera_id,
                    "pose_id": group.pose_id,
                    "roi_name": group.roi_name,
                    "split": group.split,
                    "decision": group.decision,
                    "quality_pass": group.quality_pass,
                    "label_status": group.label_status,
                    "lights": list(group.lights),
                    "model_key": selected_model_key,
                },
                "predictions": [
                    {
                        "score": candidate.score,
                        "bbox_xyxy_pixel": candidate.bbox_xyxy_pixel,
                    }
                    for candidate in candidates
                ],
                "ground_truths": [
                    {
                        "bbox_xyxy_pixel": tuple(int(value) for value in gt["bbox_xyxy_pixel"]),
                        "severity": str(gt.get("severity", "")),
                    }
                    for gt in group.ground_truths
                ],
            }
        )
    return image_predictions


def _accumulate_breakdown(target: dict[str, dict[str, int]], key: str, metrics: dict) -> None:
    key = key or "unknown"
    bucket = target.setdefault(key, {"true_positives": 0, "false_positives": 0, "false_negatives": 0})
    bucket["true_positives"] += int(metrics["true_positives"])
    bucket["false_positives"] += int(metrics["false_positives"])
    bucket["false_negatives"] += int(metrics["false_negatives"])


def _finalize_breakdown(raw: dict[str, dict[str, dict[str, int]]]) -> dict[str, dict[str, dict[str, float]]]:
    output: dict[str, dict[str, dict[str, float]]] = {}
    for breakdown_name, buckets in raw.items():
        output[breakdown_name] = {}
        for key, counts in buckets.items():
            tp = counts["true_positives"]
            fp = counts["false_positives"]
            fn = counts["false_negatives"]
            total_preds = tp + fp
            total_gts = tp + fn
            output[breakdown_name][key] = {
                "true_positives": tp,
                "false_positives": fp,
                "false_negatives": fn,
                "precision": tp / total_preds if total_preds > 0 else 1.0,
                "recall": tp / total_gts if total_gts > 0 else 1.0,
            }
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="评估检测流水线精度")
    parser.add_argument("--manifest", required=True, type=Path, help="带标注的 dataset manifest")
    parser.add_argument("--recipe", default="seat_a_black_leather_v1", help="配方 ID")
    parser.add_argument("--model-key", default=None, help="指定要评估的模型 key，默认使用配方 ROI 映射")
    parser.add_argument("--output", required=True, type=Path, help="评估报告 JSON 输出路径")
    parser.add_argument("--iou-threshold", type=float, default=0.5)
    parser.add_argument("--score-threshold", type=float, default=0.0)
    parser.add_argument("--split", default=None, help="只评估指定 split")
    args = parser.parse_args(argv)

    try:
        report = evaluate_from_manifest(
            manifest_path=args.manifest,
            output_path=args.output,
            recipe_id=args.recipe,
            iou_threshold=args.iou_threshold,
            score_threshold=args.score_threshold,
            model_key=args.model_key,
            split=args.split,
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
