from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class SegmentationCandidate:
    """统一后的 ROI segmentation 候选。"""

    bbox_xyxy: tuple[float, float, float, float]
    score: float
    class_id: int
    mask: Any
    mask_bbox_xyxy: tuple[float, float, float, float] | None = None


def decode_yolo_rows(
    output: Any,
    *,
    confidence_threshold: float,
    output_decode: str = "yolo_xyxy_rows",
) -> list[list[float]]:
    """把 YOLO/ONNX 输出统一成 [x0, y0, x1, y1, score, class_id] 行表。"""
    array = np.asarray(output, dtype=np.float32)
    if output_decode in {"yolo_xyxy_rows", "detection_rows"}:
        return _decode_row_table(array)
    if output_decode == "ultralytics_yolo":
        return _decode_ultralytics_output(array, confidence_threshold=confidence_threshold)
    raise RuntimeError(f"不支持的 YOLO 输出解码方式: {output_decode}")


def decode_yolo_segmentation(
    outputs: list[Any],
    *,
    confidence_threshold: float,
    mask_threshold: float,
    output_decode: str = "segmentation_rows",
) -> list[SegmentationCandidate]:
    """把 ROI segmentation ONNX 输出统一成带二值 mask 的候选列表。"""
    if not outputs:
        raise RuntimeError("YOLO segmentation 输出为空")
    if output_decode == "segmentation_rows":
        return _decode_segmentation_rows(
            np.asarray(outputs[0], dtype=np.float32),
            confidence_threshold=confidence_threshold,
            mask_threshold=mask_threshold,
        )
    if output_decode == "ultralytics_yolo_seg":
        if len(outputs) < 2:
            raise RuntimeError("Ultralytics YOLO segmentation 输出至少需要 boxes 和 protos")
        return _decode_ultralytics_segmentation(
            np.asarray(outputs[0], dtype=np.float32),
            np.asarray(outputs[1], dtype=np.float32),
            confidence_threshold=confidence_threshold,
            mask_threshold=mask_threshold,
        )
    raise RuntimeError(f"不支持的 YOLO segmentation 输出解码方式: {output_decode}")


def _decode_row_table(array: np.ndarray) -> list[list[float]]:
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2 or array.shape[1] < 6:
        raise RuntimeError(f"YOLO row 输出形状无效: {tuple(array.shape)}")
    return array[:, :6].tolist()


def _decode_ultralytics_output(array: np.ndarray, *, confidence_threshold: float) -> list[list[float]]:
    rows = _normalize_ultralytics_rows(array, context="Ultralytics YOLO")
    if rows.shape[1] < 5:
        raise RuntimeError(f"Ultralytics YOLO rows 输出形状无效: {tuple(rows.shape)}")
    class_scores = rows[:, 4:]
    if class_scores.shape[1] == 0:
        return []
    _ensure_finite(class_scores, "Ultralytics YOLO class scores")
    class_ids = np.argmax(class_scores, axis=1)
    scores = class_scores[np.arange(rows.shape[0]), class_ids]
    scores_f64 = scores.astype(np.float64, copy=False)
    keep = scores_f64 >= float(confidence_threshold)
    if not np.any(keep):
        return []
    xywh = rows[keep, :4].astype(np.float64, copy=False)
    _ensure_finite(xywh, "Ultralytics YOLO bbox")
    half_wh = xywh[:, 2:4] / 2.0
    xyxy = np.empty((xywh.shape[0], 4), dtype=np.float64)
    xyxy[:, 0:2] = xywh[:, 0:2] - half_wh
    xyxy[:, 2:4] = xywh[:, 0:2] + half_wh
    decoded = np.column_stack(
        (
            xyxy,
            scores_f64[keep],
            class_ids[keep].astype(np.float64, copy=False),
        )
    )
    return decoded.tolist()


def _decode_segmentation_rows(
    array: np.ndarray,
    *,
    confidence_threshold: float,
    mask_threshold: float,
) -> list[SegmentationCandidate]:
    # 参考输出格式: (N, 6 + H * W) 或 (1, N, 6 + H * W)，每行前 6 列为 xyxy/score/class_id。
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2 or array.shape[1] <= 6:
        raise RuntimeError(f"YOLO segmentation row 输出形状无效: {tuple(array.shape)}")

    score_values = array[:, 4].astype(np.float64, copy=False)
    _ensure_finite(score_values, "YOLO segmentation score")
    keep = score_values >= float(confidence_threshold)
    rows = array[keep]
    if rows.size == 0:
        return []
    _ensure_finite(rows, "YOLO segmentation kept rows")
    class_values = rows[:, 5]
    if not np.all(np.equal(class_values, np.rint(class_values))):
        invalid = float(class_values[np.flatnonzero(~np.equal(class_values, np.rint(class_values)))[0]])
        raise RuntimeError(f"YOLO segmentation class_id 不是整数: {invalid}")
    mask_values = rows[:, 6:]
    side = int(round(float(mask_values.shape[1]) ** 0.5))
    if side * side != mask_values.shape[1]:
        raise RuntimeError(f"YOLO segmentation mask 不是平方展开数组: {mask_values.shape[1]}")
    masks = (mask_values.reshape(rows.shape[0], side, side) >= float(mask_threshold)).astype(np.uint8)
    return [
        SegmentationCandidate(
            bbox_xyxy=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
            score=float(row[4]),
            class_id=int(class_value),
            mask=masks[index],
            mask_bbox_xyxy=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
        )
        for index, (row, class_value) in enumerate(zip(rows, class_values))
    ]


def _decode_ultralytics_segmentation(
    boxes_output: np.ndarray,
    protos_output: np.ndarray,
    *,
    confidence_threshold: float,
    mask_threshold: float,
) -> list[SegmentationCandidate]:
    # Ultralytics YOLOv8-seg ONNX 常见输出:
    # boxes: (1, 4 + classes + mask_dim, boxes)
    # protos: (1, mask_dim, mask_h, mask_w)
    boxes = boxes_output[0] if boxes_output.ndim == 3 and boxes_output.shape[0] == 1 else boxes_output
    protos = protos_output[0] if protos_output.ndim == 4 and protos_output.shape[0] == 1 else protos_output
    if boxes.ndim != 2:
        raise RuntimeError(f"Ultralytics YOLO segmentation boxes 输出形状无效: {tuple(boxes.shape)}")
    rows = boxes.T if boxes.shape[0] <= boxes.shape[1] and boxes.shape[0] < 256 else boxes
    if protos.ndim != 3:
        raise RuntimeError(f"Ultralytics YOLO segmentation protos 输出形状无效: {tuple(protos.shape)}")

    mask_dim = int(protos.shape[0])
    if rows.shape[1] <= 4 + mask_dim and boxes.shape[0] > 4 + mask_dim:
        rows = boxes.T
    if rows.shape[1] <= 4 + mask_dim:
        raise RuntimeError(f"Ultralytics YOLO segmentation rows 输出形状无效: {tuple(rows.shape)}, mask_dim={mask_dim}")
    class_count = rows.shape[1] - 4 - mask_dim
    if class_count <= 0:
        raise RuntimeError("Ultralytics YOLO segmentation 类别数无效")

    class_scores = rows[:, 4 : 4 + class_count]
    _ensure_finite(class_scores, "Ultralytics YOLO segmentation class scores")
    class_ids = np.argmax(class_scores, axis=1)
    scores = class_scores[np.arange(rows.shape[0]), class_ids]
    scores_f64 = scores.astype(np.float64, copy=False)
    keep = scores_f64 >= float(confidence_threshold)
    if not np.any(keep):
        return []

    kept_rows = rows[keep]
    _ensure_finite(kept_rows, "Ultralytics YOLO segmentation kept rows")
    _ensure_finite(protos, "Ultralytics YOLO segmentation protos")
    kept_scores = scores_f64[keep]
    kept_class_ids = class_ids[keep]
    xywh = kept_rows[:, :4].astype(np.float64, copy=False)
    half_wh = xywh[:, 2:4] / 2.0
    xyxy = np.empty((kept_rows.shape[0], 4), dtype=np.float64)
    xyxy[:, 0:2] = xywh[:, 0:2] - half_wh
    xyxy[:, 2:4] = xywh[:, 0:2] + half_wh

    coeffs = kept_rows[:, 4 + class_count : 4 + class_count + mask_dim]
    masks = _decode_proto_masks(coeffs, protos.reshape(mask_dim, -1), protos.shape[1], protos.shape[2], mask_threshold)
    mask_bbox = (0.0, 0.0, float(protos.shape[2] - 1), float(protos.shape[1] - 1))
    return [
        SegmentationCandidate(
            bbox_xyxy=(float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])),
            score=float(score),
            class_id=int(class_id),
            mask=masks[index],
            mask_bbox_xyxy=mask_bbox,
        )
        for index, (bbox, score, class_id) in enumerate(zip(xyxy, kept_scores, kept_class_ids))
    ]


def _normalize_ultralytics_rows(array: np.ndarray, *, context: str) -> np.ndarray:
    """将 Ultralytics YOLO ONNX 输出规范化为 (N, features) 行表。

    假定特征维度 >= 5（4 个 bbox 坐标 + 至少 1 个类别分数），检测数 N 可变。
    仅当特征维度明确在 axis=0 时才转置；不再使用启发式猜测，
    避免少检测框时错误转置导致坐标/分数互换。
    """
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2:
        raise RuntimeError(f"{context} 输出形状无效: {tuple(array.shape)}")
    if array.shape[1] >= 5:
        rows = array
    elif array.shape[0] >= 5:
        rows = array.T
    else:
        raise RuntimeError(
            f"{context} 无法确定输出方向: shape={tuple(array.shape)}，"
            f"两个维度均 < 5，不符合 YOLO 输出约定"
        )
    if rows.ndim != 2 or rows.shape[1] < 5:
        raise RuntimeError(f"{context} rows 输出形状无效: {tuple(rows.shape)}")
    return rows


def _decode_proto_masks(
    coeffs: np.ndarray,
    proto_flat: np.ndarray,
    mask_h: int,
    mask_w: int,
    mask_threshold: float,
    *,
    chunk_size: int = 64,
) -> np.ndarray:
    masks = np.empty((coeffs.shape[0], mask_h, mask_w), dtype=np.uint8)
    for start in range(0, coeffs.shape[0], chunk_size):
        end = min(start + chunk_size, coeffs.shape[0])
        logits = coeffs[start:end] @ proto_flat
        probabilities = np.float32(1.0) / (np.float32(1.0) + _safe_exp(-logits))
        masks[start:end] = (probabilities.reshape(end - start, mask_h, mask_w) >= float(mask_threshold)).astype(
            np.uint8,
            copy=False,
        )
    return masks


def _ensure_finite(array: np.ndarray, context: str) -> None:
    if array.size == 0:
        return
    finite = np.isfinite(array)
    if bool(np.all(finite)):
        return
    invalid = array.reshape(-1)[int(np.flatnonzero(~finite.reshape(-1))[0])]
    raise RuntimeError(f"{context} 包含非有限值: {float(invalid)}")


def _safe_exp(array: Any) -> Any:
    return np.exp(np.clip(array, -80.0, 80.0))
