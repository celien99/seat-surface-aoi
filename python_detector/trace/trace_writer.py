from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np

from python_detector.config.recipe_schema import Recipe
from python_detector.image_codec import write_gray_png, write_rgb_png
from python_detector.ipc.data_types import DefectResult, InspectionResult, LightFrame, SeatInspectionJob


class TraceWriter:
    def __init__(self, root_dir: str | Path = "trace") -> None:
        self.root_dir = Path(root_dir)

    def write(
        self,
        job: SeatInspectionJob,
        recipe: Recipe,
        result: InspectionResult,
        context: dict[str, Any],
    ) -> Path | None:
        if not self._should_write(job, recipe, result):
            return None

        day = datetime.now().strftime("%Y%m%d")
        safe_seat_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in job.seat_id)
        trace_dir = self.root_dir / day / f"{safe_seat_id}_{job.sequence_id}"
        trace_dir.mkdir(parents=True, exist_ok=True)

        self._write_json(trace_dir / "job.json", job)
        self._write_json(trace_dir / "result.json", result)
        self._write_json(trace_dir / "recipe_summary.json", {"recipe_id": recipe.recipe_id, "sku": recipe.sku})
        self._write_json(trace_dir / "quality_report.json", context.get("quality_report"))
        self._write_json(trace_dir / "roi_location_report.json", context.get("roi_location_reports", []))
        self._write_json(trace_dir / "registration_report.json", context.get("registration_reports", []))
        self._write_json(trace_dir / "feature_summary.json", context.get("feature_summary", []))
        self._write_json(trace_dir / "fusion_summary.json", context.get("fusion_summary", {}))
        self._write_json(trace_dir / "timings.json", context.get("timings", {}))
        self._write_json(trace_dir / "error.json", context.get("error", {}))
        self._write_raw_images(trace_dir, job)
        self._write_roi_images(trace_dir, context.get("prepared_bundles", []))
        self._write_detection_overlays(trace_dir, result, context.get("prepared_bundles", []), context.get("spatial_maps", []), job)
        return trace_dir

    def _should_write(self, job: SeatInspectionJob, recipe: Recipe, result: InspectionResult) -> bool:
        if not recipe.trace.enabled:
            return False
        if result.decision == "OK":
            ratio = max(0.0, min(float(recipe.trace.save_ok_ratio), 1.0))
            if ratio <= 0.0:
                return False
            if ratio >= 1.0:
                return True
            return self._stable_sample_score(job, recipe) < ratio
        if result.decision == "NG":
            return recipe.trace.save_ng
        if result.decision in {"RECHECK", "ERROR"}:
            return recipe.trace.save_recheck
        return True

    def _stable_sample_score(self, job: SeatInspectionJob, recipe: Recipe) -> float:
        key = f"{recipe.recipe_id}|{job.sku}|{job.seat_id}|{job.sequence_id}|{job.trigger_id}".encode("utf-8")
        digest = hashlib.sha256(key).digest()
        value = int.from_bytes(digest[:8], byteorder="big", signed=False)
        return value / float(1 << 64)

    def _write_json(self, path: Path, value: Any) -> None:
        path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_roi_images(self, trace_dir: Path, prepared_bundles: Any) -> None:
        for bundle in prepared_bundles or []:
            for roi_name, frames in getattr(bundle, "rois", {}).items():
                for light_id, frame in frames.items():
                    self._write_gray_image(
                        trace_dir
                        / "images"
                        / _safe_name(bundle.camera_id)
                        / _safe_name(getattr(bundle, "pose_id", "") or bundle.camera_id)
                        / _safe_name(roi_name)
                        / f"{_safe_name(light_id)}.png",
                        frame,
                    )

    def _write_raw_images(self, trace_dir: Path, job: SeatInspectionJob) -> None:
        for bundle in job.camera_bundles:
            pose_id = bundle.pose_id or bundle.camera_id
            for light_id, frame in bundle.light_frames.items():
                self._write_gray_image(
                    trace_dir
                    / "raw_images"
                    / _safe_name(bundle.camera_id)
                    / _safe_name(pose_id)
                    / f"{_safe_name(light_id)}.png",
                    frame,
                )

    def _raw_frame_index(self, job: SeatInspectionJob) -> dict[tuple[str, str, str], LightFrame]:
        """构建 raw 帧索引: (camera_id, pose_id, light_id) -> LightFrame。"""
        index: dict[tuple[str, str, str], LightFrame] = {}
        for bundle in job.camera_bundles:
            pose_id = bundle.pose_id or bundle.camera_id
            for light_id, frame in bundle.light_frames.items():
                index[(bundle.camera_id, pose_id, light_id)] = frame
        return index

    def _write_detection_overlays(
        self,
        trace_dir: Path,
        result: InspectionResult,
        prepared_bundles: Any,
        spatial_maps: list[dict[str, object]],
        job: SeatInspectionJob,
    ) -> None:
        frame_groups = self._roi_frame_groups(prepared_bundles)
        if not frame_groups:
            return
        raw_index = self._raw_frame_index(job)
        defects_by_roi = self._defects_by_roi(result.defects)
        anomaly_maps = self._anomaly_map_index(spatial_maps)
        overlay_dir = trace_dir / "overlays"
        for key in sorted(frame_groups):
            camera_id, pose_id, roi_name = key
            frames = frame_groups[key]
            roi_defects = defects_by_roi.get(key, [])
            roi_frame = self._display_frame(frames, roi_defects)
            if roi_frame is None:
                continue
            raw_frame = raw_index.get((camera_id, pose_id, roi_frame.light_id))
            if raw_frame is None:
                for (raw_camera_id, raw_pose_id, _raw_light_id), fallback_frame in raw_index.items():
                    if raw_camera_id == camera_id and raw_pose_id == pose_id:
                        raw_frame = fallback_frame
                        break
            if raw_frame is None:
                continue
            anomaly_entry = anomaly_maps.get(key)
            path = overlay_dir / _safe_name(camera_id) / _safe_name(pose_id) / f"{_safe_name(roi_name)}.png"
            if anomaly_entry is not None:
                self._write_heatmap_overlay_on_raw(path, raw_frame, roi_frame, result.decision, roi_defects, anomaly_entry)
            else:
                self._write_overlay_png_on_raw(path, raw_frame, result.decision, roi_defects)

    def _roi_frame_groups(self, prepared_bundles: Any) -> dict[tuple[str, str, str], dict[str, LightFrame]]:
        groups: dict[tuple[str, str, str], dict[str, LightFrame]] = {}
        for bundle in prepared_bundles or []:
            pose_id = getattr(bundle, "pose_id", "") or bundle.camera_id
            for roi_name, frames in getattr(bundle, "rois", {}).items():
                groups[(bundle.camera_id, pose_id, roi_name)] = dict(frames)
        return groups

    def _defects_by_roi(self, defects: list[DefectResult]) -> dict[tuple[str, str, str], list[DefectResult]]:
        grouped: dict[tuple[str, str, str], list[DefectResult]] = {}
        for defect in defects:
            key = (defect.camera_id, defect.pose_id or defect.camera_id, defect.roi_name)
            grouped.setdefault(key, []).append(defect)
        return grouped

    def _display_frame(self, frames: dict[str, LightFrame], defects: list[DefectResult]) -> LightFrame | None:
        for defect in defects:
            for light_id in defect.evidence_lights:
                frame = frames.get(light_id)
                if frame is not None:
                    return frame
        return frames.get("DIFFUSE") or next(iter(frames.values()), None)

    def _write_gray_image(self, path: Path, frame: LightFrame) -> None:
        write_gray_png(path, frame.width, frame.height, self._frame_bytes(frame))

    def _write_overlay_png_on_raw(
        self,
        path: Path,
        raw_frame: LightFrame,
        result_decision: str,
        defects: list[DefectResult],
    ) -> None:
        """仅绘制 raw 底图 + 决策边框 + 缺陷 bbox。"""
        raw_array = self._frame_array(raw_frame)
        raw_h, raw_w = raw_array.shape
        rgb = np.repeat(raw_array[:, :, None], 3, axis=2)
        self._draw_overlay_marks_raw(rgb, raw_w, raw_h, result_decision, defects)
        write_rgb_png(path, raw_w, raw_h, np.ascontiguousarray(rgb).tobytes())

    def _anomaly_map_index(
        self,
        spatial_maps: list[dict[str, object]],
    ) -> dict[tuple[str, str, str], dict[str, object]]:
        """从 spatial_maps 提取 anomaly_map 索引。"""
        index: dict[tuple[str, str, str], dict[str, object]] = {}
        for entry in spatial_maps or []:
            if not isinstance(entry, dict):
                continue
            anomaly_map = entry.get("anomaly_map")
            spatial_shape_raw = entry.get("spatial_shape")
            if anomaly_map is None or spatial_shape_raw is None:
                continue
            if not isinstance(spatial_shape_raw, (list, tuple)) or len(spatial_shape_raw) != 2:
                continue
            spatial_shape = (int(spatial_shape_raw[0]), int(spatial_shape_raw[1]))
            camera_id = str(entry.get("camera_id", ""))
            pose_id = str(entry.get("pose_id", "") or camera_id)
            roi_name = str(entry.get("roi_name", ""))
            if camera_id and roi_name:
                anomaly_map_tuple = tuple(tuple(float(value) for value in row) for row in anomaly_map)
                index[(camera_id, pose_id, roi_name)] = {
                    "anomaly_map": anomaly_map_tuple,
                    "spatial_shape": spatial_shape,
                    "score_threshold": _optional_float(entry.get("score_threshold")),
                    "anomaly_binarize_min_ratio": _optional_float(entry.get("anomaly_binarize_min_ratio")),
                    "anomaly_binarize_relative": _optional_float(entry.get("anomaly_binarize_relative")),
                }
        return index

    def _write_heatmap_overlay_on_raw(
        self,
        path: Path,
        raw_frame: LightFrame,
        roi_frame: LightFrame,
        result_decision: str,
        defects: list[DefectResult],
        anomaly_entry: dict[str, object],
    ) -> None:
        """将阈值以上的 PatchCore 热区叠加到 raw 原图，并绘制判定框和缺陷框。"""
        raw_array = self._frame_array(raw_frame)
        raw_h, raw_w = raw_array.shape
        roi_w, roi_h = roi_frame.width, roi_frame.height
        rgb = np.repeat(raw_array[:, :, None], 3, axis=2)

        anomaly_map = anomaly_entry.get("anomaly_map")
        anomaly_array = _resize_anomaly_map_array(anomaly_map, roi_h, roi_w)
        if anomaly_array.size > 0:
            hot_mask, normalized = _thresholded_anomaly_heatmap(
                anomaly_array,
                _optional_float(anomaly_entry.get("score_threshold")),
                _optional_float(anomaly_entry.get("anomaly_binarize_min_ratio")),
                _optional_float(anomaly_entry.get("anomaly_binarize_relative")),
            )
            ix, iy, source_indices = self._roi_to_raw_indices(roi_frame, raw_w, raw_h)
            if ix.size > 0:
                flat_indices = (iy * raw_w) + ix
                order = _last_occurrence_order(flat_indices)
                target_indices = flat_indices[order]
                defect_values = _points_inside_defects(ix[order], iy[order], defects)
                hot_values = hot_mask.reshape(-1)[source_indices][order] & defect_values
                if np.any(hot_values):
                    hot_target_indices = target_indices[hot_values]
                    heat_values = normalized.reshape(-1)[source_indices][order][hot_values]
                    gray_values = raw_array.reshape(-1)[hot_target_indices]
                    heat_rgb = _hot_colormap_array(heat_values)
                    blended = (
                        heat_rgb.astype(np.float32) * 0.7 + gray_values[:, None].astype(np.float32) * 0.3
                    ).astype(
                        np.uint8,
                        copy=False,
                    )
                    rgb.reshape(-1, 3)[hot_target_indices] = blended

        self._draw_overlay_marks_raw(rgb, raw_w, raw_h, result_decision, defects)
        write_rgb_png(path, raw_w, raw_h, np.ascontiguousarray(rgb).tobytes())

    def _roi_to_raw_indices(self, roi_frame: LightFrame, raw_w: int, raw_h: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        roi_h, roi_w = roi_frame.height, roi_frame.width
        yy, xx = np.indices((roi_h, roi_w), dtype=np.float64)
        matrix = roi_frame.roi_to_source_matrix
        if matrix is not None and len(matrix) == 9:
            denom = matrix[6] * xx + matrix[7] * yy + matrix[8]
            valid = np.abs(denom) >= 1e-9
            sx = np.empty_like(xx)
            sy = np.empty_like(yy)
            sx[valid] = (matrix[0] * xx[valid] + matrix[1] * yy[valid] + matrix[2]) / denom[valid]
            sy[valid] = (matrix[3] * xx[valid] + matrix[4] * yy[valid] + matrix[5]) / denom[valid]
        else:
            origin_x, origin_y = roi_frame.origin_xy
            sx = xx + float(origin_x)
            sy = yy + float(origin_y)
            valid = np.ones((roi_h, roi_w), dtype=bool)

        ix = np.rint(sx[valid]).astype(np.intp)
        iy = np.rint(sy[valid]).astype(np.intp)
        source_indices = np.flatnonzero(valid)
        inside = (ix >= 0) & (ix < raw_w) & (iy >= 0) & (iy < raw_h)
        return ix[inside], iy[inside], source_indices[inside]

    def _draw_overlay_marks_raw(
        self,
        rgb: np.ndarray,
        width: int,
        height: int,
        result_decision: str,
        defects: list[DefectResult],
    ) -> None:
        """在 raw 图像上绘制决策边框和缺陷 bbox。"""
        self._draw_rect(rgb, width, height, 0, 0, width - 1, height - 1, _decision_color(result_decision), thickness=4)
        for defect in defects:
            x0, y0, x1, y1 = defect.bbox_xyxy_pixel
            self._draw_rect(rgb, width, height, x0, y0, x1, y1, _decision_color(defect.decision), thickness=3)

    def _draw_rect(
        self,
        rgb: np.ndarray,
        width: int,
        height: int,
        x0: int,
        y0: int,
        x1: int,
        y1: int,
        color: tuple[int, int, int],
        *,
        thickness: int,
    ) -> None:
        x0 = max(0, min(width - 1, x0))
        x1 = max(0, min(width - 1, x1))
        y0 = max(0, min(height - 1, y0))
        y1 = max(0, min(height - 1, y1))
        if x1 < x0 or y1 < y0:
            return
        color_array = np.asarray(color, dtype=np.uint8)
        top_end = min(y1 + 1, y0 + thickness)
        bottom_start = max(y0, y1 - thickness + 1)
        left_end = min(x1 + 1, x0 + thickness)
        right_start = max(x0, x1 - thickness + 1)
        rgb[y0:top_end, x0 : x1 + 1] = color_array
        rgb[bottom_start : y1 + 1, x0 : x1 + 1] = color_array
        rgb[y0 : y1 + 1, x0:left_end] = color_array
        rgb[y0 : y1 + 1, right_start : x1 + 1] = color_array

    def _frame_bytes(self, frame: LightFrame) -> bytes:
        return np.ascontiguousarray(self._frame_array(frame)).tobytes()

    def _frame_array(self, frame: LightFrame) -> np.ndarray:
        if frame.dtype != "UINT8" or frame.channels != 1:
            raise ValueError(f"trace 仅支持 UINT8 MONO ROI: {frame.camera_id}/{frame.light_id}")
        expected = frame.stride_bytes * frame.height
        if len(frame.image) < expected:
            raise ValueError(f"trace ROI 图像长度不足: {frame.camera_id}/{frame.light_id}")
        raw = np.frombuffer(frame.image, dtype=np.uint8, count=expected)
        return raw.reshape(frame.height, frame.stride_bytes)[:, : frame.width]

def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, memoryview):
        return {"memoryview_bytes": len(value)}
    if hasattr(value, "value"):
        return value.value
    return value


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(result):
        return None
    return result


def _decision_color(decision: str) -> tuple[int, int, int]:
    return {
        "OK": (0, 180, 90),
        "RECHECK": (255, 190, 40),
        "NG": (255, 64, 64),
        "ERROR": (180, 80, 255),
    }.get(decision, (255, 255, 255))


def _hot_colormap_array(values: np.ndarray) -> np.ndarray:
    """缺陷热区色图: [0, 1] -> 黄/橙/红，避免低分区域显示成蓝色。"""
    clipped = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    result = np.empty((clipped.size, 3), dtype=np.float32)
    result[:, 0] = 255.0
    result[:, 1] = 210.0 * (1.0 - clipped)
    result[:, 2] = 32.0 * (1.0 - clipped)
    return np.clip(result, 0.0, 255.0).astype(np.uint8)


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
    except Exception:
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
    except Exception:
        return mask


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
