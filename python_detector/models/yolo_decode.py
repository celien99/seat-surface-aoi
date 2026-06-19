from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError("numpy 未安装，无法解析 YOLO 输出") from exc

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
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError("numpy 未安装，无法解析 YOLO segmentation 输出") from exc

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


def _decode_row_table(array: Any) -> list[list[float]]:
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2 or array.shape[1] < 6:
        raise RuntimeError(f"YOLO row 输出形状无效: {tuple(array.shape)}")
    return array[:, :6].tolist()


def _decode_ultralytics_output(array: Any, *, confidence_threshold: float) -> list[list[float]]:
    # Ultralytics detection ONNX 通常为 (1, 4 + classes, boxes)，也可能导出为 (1, boxes, 4 + classes)。
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2:
        raise RuntimeError(f"Ultralytics YOLO 输出形状无效: {tuple(array.shape)}")
    if array.shape[0] < 5 and array.shape[1] >= 5:
        rows = array
    elif array.shape[1] < 5 and array.shape[0] >= 5:
        rows = array.T
    elif array.shape[0] <= array.shape[1] and array.shape[0] < 128:
        rows = array.T
    else:
        rows = array
    if rows.ndim != 2 or rows.shape[1] < 5:
        raise RuntimeError(f"Ultralytics YOLO rows 输出形状无效: {tuple(rows.shape)}")

    decoded: list[list[float]] = []
    for raw in rows:
        x_center, y_center, width, height = (float(value) for value in raw[:4])
        class_scores = raw[4:]
        if class_scores.size == 0:
            continue
        class_id = int(class_scores.argmax())
        score = float(class_scores[class_id])
        if score < confidence_threshold:
            continue
        x0 = x_center - width / 2.0
        y0 = y_center - height / 2.0
        x1 = x_center + width / 2.0
        y1 = y_center + height / 2.0
        decoded.append([x0, y0, x1, y1, score, float(class_id)])
    return decoded


def _decode_segmentation_rows(
    array: Any,
    *,
    confidence_threshold: float,
    mask_threshold: float,
) -> list[SegmentationCandidate]:
    # 参考输出格式: (N, 6 + H * W) 或 (1, N, 6 + H * W)，每行前 6 列为 xyxy/score/class_id。
    if array.ndim == 3 and array.shape[0] == 1:
        array = array[0]
    if array.ndim != 2 or array.shape[1] <= 6:
        raise RuntimeError(f"YOLO segmentation row 输出形状无效: {tuple(array.shape)}")

    candidates: list[SegmentationCandidate] = []
    for row in array:
        score = float(row[4])
        if score < confidence_threshold:
            continue
        class_value = float(row[5])
        if not class_value.is_integer():
            raise RuntimeError(f"YOLO segmentation class_id 不是整数: {class_value}")
        mask_values = row[6:]
        side = int(round(float(mask_values.size) ** 0.5))
        if side * side != mask_values.size:
            raise RuntimeError(f"YOLO segmentation mask 不是平方展开数组: {mask_values.size}")
        mask = (mask_values.reshape(side, side) >= mask_threshold).astype("uint8")
        candidates.append(
            SegmentationCandidate(
                bbox_xyxy=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
                score=score,
                class_id=int(class_value),
                mask=mask,
                mask_bbox_xyxy=(float(row[0]), float(row[1]), float(row[2]), float(row[3])),
            )
        )
    return candidates


def _decode_ultralytics_segmentation(
    boxes_output: Any,
    protos_output: Any,
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
    if boxes.shape[0] <= boxes.shape[1] and boxes.shape[0] < 256:
        rows = boxes.T
    else:
        rows = boxes
    if protos.ndim != 3:
        raise RuntimeError(f"Ultralytics YOLO segmentation protos 输出形状无效: {tuple(protos.shape)}")

    mask_dim = int(protos.shape[0])
    if rows.shape[1] <= 4 + mask_dim:
        raise RuntimeError(
            f"Ultralytics YOLO segmentation rows 输出形状无效: {tuple(rows.shape)}, mask_dim={mask_dim}"
        )
    class_count = rows.shape[1] - 4 - mask_dim
    if class_count <= 0:
        raise RuntimeError("Ultralytics YOLO segmentation 类别数无效")

    proto_flat = protos.reshape(mask_dim, -1)
    candidates: list[SegmentationCandidate] = []
    for raw in rows:
        x_center, y_center, width, height = (float(value) for value in raw[:4])
        class_scores = raw[4 : 4 + class_count]
        class_id = int(class_scores.argmax())
        score = float(class_scores[class_id])
        if score < confidence_threshold:
            continue
        coeffs = raw[4 + class_count : 4 + class_count + mask_dim]
        logits = coeffs @ proto_flat
        probabilities = 1.0 / (1.0 + _safe_exp(-logits))
        mask = (probabilities.reshape(protos.shape[1], protos.shape[2]) >= mask_threshold).astype("uint8")
        candidates.append(
            SegmentationCandidate(
                bbox_xyxy=(
                    x_center - width / 2.0,
                    y_center - height / 2.0,
                    x_center + width / 2.0,
                    y_center + height / 2.0,
                ),
                score=score,
                class_id=class_id,
                mask=mask,
                mask_bbox_xyxy=(0.0, 0.0, float(protos.shape[2] - 1), float(protos.shape[1] - 1)),
            )
        )
    return candidates


def _safe_exp(array: Any) -> Any:
    try:
        import numpy as np  # type: ignore
    except Exception as exc:
        raise RuntimeError("numpy 未安装，无法解析 YOLO segmentation 输出") from exc
    return np.exp(np.clip(array, -80.0, 80.0))
