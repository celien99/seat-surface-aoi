from __future__ import annotations

from typing import Any


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
