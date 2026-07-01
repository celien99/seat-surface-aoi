"""空间 PatchCore 异常图工具函数：缺陷候选、bbox 映射、连通域提取、数组校验。

从 inference_engine.py 抽取以控制文件规模。
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np

from python_detector.ipc.data_types import apply_homography
from python_detector.pipeline.feature_builder import FeatureGroup


@dataclass
class DefectCandidate:
    camera_id: str
    roi_name: str
    score: float
    bbox_xyxy_pixel: tuple[int, int, int, int]
    area_px: int
    evidence_lights: list[str]
    pose_id: str = ""
    recheck_score: float | None = None
    ng_score: float | None = None
    threshold_source: str = ""


def _map_roi_bbox_to_source(
    roi_bbox_xyxy_pixel: tuple[float, float, float, float],
    feature_group: FeatureGroup,
) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = roi_bbox_xyxy_pixel
    matrix = feature_group.roi_to_source_matrix
    if matrix is None:
        roi_x0, roi_y0, _roi_x1, _roi_y1 = feature_group.roi_bbox_xyxy_pixel
        return (
            int(round(roi_x0 + x0)),
            int(round(roi_y0 + y0)),
            int(round(roi_x0 + x1)),
            int(round(roi_y0 + y1)),
        )

    corners = (
        (x0, y0),
        (x1, y0),
        (x1, y1),
        (x0, y1),
    )
    mapped_points = [apply_homography(matrix, x, y) for x, y in corners]
    if any(point is None for point in mapped_points):
        raise RuntimeError("ROI 到原图 bbox 映射矩阵无效")
    xs = [point[0] for point in mapped_points if point is not None]
    ys = [point[1] for point in mapped_points if point is not None]
    roi_x0, roi_y0, roi_x1, roi_y1 = feature_group.roi_bbox_xyxy_pixel
    return (
        int(max(roi_x0, min(roi_x1, math.floor(min(xs))))),
        int(max(roi_y0, min(roi_y1, math.floor(min(ys))))),
        int(max(roi_x0, min(roi_x1, math.ceil(max(xs))))),
        int(max(roi_y0, min(roi_y1, math.ceil(max(ys))))),
    )


def _anomaly_map_bboxes(
    anomaly_map: "np.ndarray",
    spatial_shape: tuple[int, int],
    recheck_score: float,
    feature_group: FeatureGroup,
    *,
    binarize_min_ratio: float = 0.5,
    binarize_relative: float = 0.3,
) -> list[tuple[tuple[int, int, int, int], float]]:
    """从 anomaly_map 提取连通域 bbox 列表，按分数降序排列。

    返回 [(bbox_xyxy_source, max_score), ...]，坐标已映射到原图空间。
    使用 scipy.ndimage 进行向量化连通域分析，避免原生 Python BFS。

    binarize_min_ratio: 二值化阈值 = max(recheck_score * min_ratio, max_anomaly * relative)
    binarize_relative: 相对峰值系数，控制异常区域检测的敏感度
    """
    from scipy import ndimage

    anomaly_map = _as_anomaly_map_array(anomaly_map)
    h_out, w_out = anomaly_map.shape
    roi_h, roi_w = feature_group.feature_shape_hw
    if roi_h <= 0 or roi_w <= 0 or h_out <= 0 or w_out <= 0:
        return []

    max_anomaly = float(anomaly_map.max())
    threshold = max(recheck_score * binarize_min_ratio, max_anomaly * binarize_relative)

    # 向量化二值掩码
    binary = anomaly_map >= threshold

    # scipy 连通域标记（C 级别实现）
    labeled, num_features = ndimage.label(binary)
    if num_features == 0:
        return []

    # 获取每个连通域的 bbox slice
    slices = ndimage.find_objects(labeled)

    x_scale = roi_w / w_out
    y_scale = roi_h / h_out
    results: list[tuple[tuple[int, int, int, int], float]] = []

    for i, sl in enumerate(slices):
        if sl is None:
            continue
        # 仅取当前连通域内的像素计算分数
        comp_mask = labeled[sl] == (i + 1)
        comp_score = float(anomaly_map[sl][comp_mask].max())

        # 映射到 ROI 特征空间（sl[0]=row_slice, sl[1]=col_slice）
        roi_x0 = sl[1].start * x_scale
        roi_y0 = sl[0].start * y_scale
        roi_x1 = sl[1].stop * x_scale
        roi_y1 = sl[0].stop * y_scale

        # 映射到原图空间
        bbox_source = _map_roi_bbox_to_source(
            (roi_x0, roi_y0, roi_x1, roi_y1),
            feature_group,
        )
        results.append((bbox_source, comp_score))

    # 按分数降序
    results.sort(key=lambda item: item[1], reverse=True)
    return results


def _as_anomaly_map_array(value: Any, *, name: str = "anomaly_map") -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    while array.ndim > 2 and array.shape[0] == 1:
        array = array[0]
    while array.ndim > 2 and array.shape[-1] == 1:
        array = array[..., 0]
    if array.ndim != 2:
        raise RuntimeError(f"PatchCore {name} 必须是 2 维矩阵，实际 shape={array.shape}")
    if array.size == 0:
        raise RuntimeError(f"PatchCore {name} 为空")
    if not np.isfinite(array).all():
        raise RuntimeError(f"PatchCore {name} 包含非有限值")
    return array
