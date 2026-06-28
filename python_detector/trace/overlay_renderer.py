"""检测 overlay PNG 渲染：热力图上采样、高斯平滑、形态学闭运算、色图映射。

从 trace_writer.py 抽取以控制文件规模。所有函数使用 numpy 批量处理，
不引入额外的图像编码依赖。
"""

from __future__ import annotations

import numpy as np

from python_detector.ipc.data_types import DefectResult


# ---------------------------------------------------------------------------
# 判定颜色映射
# ---------------------------------------------------------------------------


def _decision_color(decision: str) -> tuple[int, int, int]:
    return {
        "OK": (0, 180, 90),
        "RECHECK": (255, 190, 40),
        "NG": (255, 64, 64),
        "ERROR": (180, 80, 255),
    }.get(decision, (255, 255, 255))


# ---------------------------------------------------------------------------
# 热力色图
# ---------------------------------------------------------------------------


def _hot_colormap_array(values: np.ndarray) -> np.ndarray:
    """缺陷热区色图: [0, 1] -> 黄/橙/红，避免低分区域显示成蓝色。"""
    clipped = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    result = np.empty((clipped.size, 3), dtype=np.float32)
    result[:, 0] = 255.0
    result[:, 1] = 210.0 * (1.0 - clipped)
    result[:, 2] = 32.0 * (1.0 - clipped)
    return np.clip(result, 0.0, 255.0).astype(np.uint8)


# ---------------------------------------------------------------------------
# 热力图平滑与二值化
# ---------------------------------------------------------------------------


def _thresholded_anomaly_heatmap(
    anomaly_array: np.ndarray,
    score_threshold: float | None,
    binarize_min_ratio: float | None,
    binarize_relative: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    """返回需要显示的异常掩码，以及阈值以上归一化热度。

    热力图平滑管线：
    1. 高斯滤波平滑 → 消除特征提取和插值残余的栅格感
    2. 自适应阈值二值化 → 分离异常/正常区域
    3. 形态学闭运算 → 填补二值掩码中的小孔洞，平滑边界
    """
    values = np.asarray(anomaly_array, dtype=np.float32)
    if values.size == 0:
        return np.zeros(values.shape, dtype=bool), np.zeros(values.shape, dtype=np.float32)
    finite = np.isfinite(values)
    if not np.any(finite):
        return np.zeros(values.shape, dtype=bool), np.zeros(values.shape, dtype=np.float32)

    # ① 高斯平滑：消除块状马赛克残余
    smoothed = _gaussian_blur_anomaly(values)

    finite_values = smoothed[finite]
    max_value = float(finite_values.max())
    if max_value <= 0.0:
        return np.zeros(values.shape, dtype=bool), np.zeros(values.shape, dtype=np.float32)

    min_ratio = 0.5 if binarize_min_ratio is None else max(0.0, float(binarize_min_ratio))
    relative = 0.3 if binarize_relative is None else max(0.0, float(binarize_relative))
    threshold = max_value * relative
    if score_threshold is not None and score_threshold > 0.0:
        threshold = max(threshold, float(score_threshold) * min_ratio)
    threshold = min(threshold, max_value)

    hot_mask = finite & (smoothed >= threshold)

    # ② 形态学闭运算：填充掩码内部小孔洞，使热区更连贯
    hot_mask = _morphology_close(hot_mask)

    normalized = np.zeros(values.shape, dtype=np.float32)
    denom = max(max_value - threshold, 1e-6)
    normalized[hot_mask] = 0.35 + 0.65 * np.clip((smoothed[hot_mask] - threshold) / denom, 0.0, 1.0)
    return hot_mask, normalized


def _gaussian_blur_anomaly(array: np.ndarray) -> np.ndarray:
    """对异常图应用轻度高斯模糊，消除上采样残余的栅格/马赛克感。

    自适应 sigma：较大尺寸的 ROI 需要稍大的平滑半径。
    回退方案：如果 scipy 不可用，使用 3×3 盒式滤波。
    """
    try:
        from scipy.ndimage import gaussian_filter

        h, w = array.shape
        # 自适应 sigma: 较大图像用稍大的平滑半径，但不超过 1.5 像素
        sigma = min(max(h, w) / 800.0, 1.5)
        return gaussian_filter(array, sigma=sigma, mode="nearest")
    except ImportError:
        # numpy 回退: 3×3 均值卷积 (可分离)
        kernel = np.ones((3, 3), dtype=np.float32) / 9.0
        pad = np.pad(array, ((1, 1), (1, 1)), mode="edge")
        result = np.zeros_like(array)
        for dy in range(3):
            for dx in range(3):
                result += pad[dy : dy + array.shape[0], dx : dx + array.shape[1]] * kernel[dy, dx]
        return result


def _morphology_close(mask: np.ndarray) -> np.ndarray:
    """对二值掩码做形态学闭运算：先膨胀再腐蚀，填补小孔洞并平滑边界。

    使用 3×3 交叉结构元素。回退：scipy 不可用时返回原始掩码。
    """
    if not np.any(mask):
        return mask
    try:
        from scipy.ndimage import binary_closing

        # 3×3 交叉结构元素 (4-邻域 + 中心)
        structure = np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]], dtype=bool)
        return binary_closing(mask, structure=structure, iterations=1, border_value=False)
    except ImportError:
        return mask


# ---------------------------------------------------------------------------
# 缺陷区域判定与异常图上采样
# ---------------------------------------------------------------------------


def _points_inside_defects(x: np.ndarray, y: np.ndarray, defects: list[DefectResult]) -> np.ndarray:
    if x.size == 0 or not defects:
        return np.zeros(x.shape, dtype=bool)
    mask = np.zeros(x.shape, dtype=bool)
    for defect in defects:
        x0, y0, x1, y1 = defect.bbox_xyxy_pixel
        if x1 < x0 or y1 < y0:
            continue
        mask |= (x >= x0) & (x <= x1) & (y >= y0) & (y <= y1)
    return mask


def _resize_anomaly_map(
    anomaly_map: tuple[tuple[float, ...], ...],
    src_shape: tuple[int, int],
    target_h: int,
    target_w: int,
) -> list[list[float]]:
    """双线性上采样 anomaly_map 到目标尺寸 [target_h, target_w]。"""
    src_h, src_w = src_shape
    if src_h <= 0 or src_w <= 0:
        return [[0.0 for _x in range(target_w)] for _y in range(target_h)]
    return _resize_anomaly_map_array(anomaly_map, target_h, target_w).tolist()


def _resize_anomaly_map_array(
    anomaly_map: tuple[tuple[float, ...], ...],
    target_h: int,
    target_w: int,
) -> np.ndarray:
    """双线性插值将 anomaly_map 缩放到目标尺寸，消除块状马赛克效应。

    使用像素中心对齐 (half-pixel offset) 的双线性插值，
    替代原来的最近邻上采样，生成平滑连续的热力图过渡。
    """
    source = np.asarray(anomaly_map, dtype=np.float32)
    if source.ndim != 2 or source.shape[0] <= 0 or source.shape[1] <= 0 or target_h <= 0 or target_w <= 0:
        return np.zeros((max(target_h, 0), max(target_w, 0)), dtype=np.float32)
    if source.shape == (target_h, target_w):
        return source

    src_h, src_w = source.shape

    # 半像素中心对齐: 目标像素中心映射回源图浮点坐标
    y_coords = (np.arange(target_h, dtype=np.float64) + 0.5) * src_h / target_h - 0.5
    x_coords = (np.arange(target_w, dtype=np.float64) + 0.5) * src_w / target_w - 0.5

    # 四个角点的整数索引
    y0 = np.clip(np.floor(y_coords).astype(np.intp), 0, src_h - 1)
    x0 = np.clip(np.floor(x_coords).astype(np.intp), 0, src_w - 1)
    y1 = np.clip(y0 + 1, 0, src_h - 1)
    x1 = np.clip(x0 + 1, 0, src_w - 1)

    # 小数部分权重
    wy = (y_coords - y0.astype(np.float64)).astype(np.float32)[:, None]  # [H, 1]
    wx = (x_coords - x0.astype(np.float64)).astype(np.float32)[None, :]  # [1, W]

    w00 = (1.0 - wy) * (1.0 - wx)
    w01 = (1.0 - wy) * wx
    w10 = wy * (1.0 - wx)
    w11 = wy * wx

    result = (
        source[y0[:, None], x0[None, :]] * w00
        + source[y0[:, None], x1[None, :]] * w01
        + source[y1[:, None], x0[None, :]] * w10
        + source[y1[:, None], x1[None, :]] * w11
    )
    return result.astype(np.float32, copy=False)


def _last_occurrence_order(indices: np.ndarray) -> np.ndarray:
    if indices.size == 0:
        return indices.astype(np.intp, copy=False)
    reversed_indices = indices[::-1]
    _unique_values, first_reversed = np.unique(reversed_indices, return_index=True)
    last_positions = indices.size - 1 - first_reversed
    return np.sort(last_positions).astype(np.intp, copy=False)
