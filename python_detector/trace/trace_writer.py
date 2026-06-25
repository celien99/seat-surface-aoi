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
                anomaly_map, _spatial_shape = anomaly_entry
                self._write_heatmap_overlay_on_raw(path, raw_frame, roi_frame, result.decision, roi_defects, anomaly_map)
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
    ) -> dict[tuple[str, str, str], tuple[tuple[tuple[float, ...], ...], tuple[int, int]]]:
        """从 spatial_maps 提取 anomaly_map 索引。"""
        index: dict[tuple[str, str, str], tuple[tuple[tuple[float, ...], ...], tuple[int, int]]] = {}
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
                index[(camera_id, pose_id, roi_name)] = (anomaly_map_tuple, spatial_shape)
        return index

    def _write_heatmap_overlay_on_raw(
        self,
        path: Path,
        raw_frame: LightFrame,
        roi_frame: LightFrame,
        result_decision: str,
        defects: list[DefectResult],
        anomaly_map: tuple[tuple[float, ...], ...],
    ) -> None:
        """将 JET 热力图叠加到 raw 原图，并绘制判定框和缺陷框。"""
        raw_array = self._frame_array(raw_frame)
        raw_h, raw_w = raw_array.shape
        roi_w, roi_h = roi_frame.width, roi_frame.height
        rgb = np.repeat(raw_array[:, :, None], 3, axis=2)

        anomaly_array = _resize_anomaly_map_array(anomaly_map, roi_h, roi_w)
        if anomaly_array.size > 0:
            ix, iy, source_indices = self._roi_to_raw_indices(roi_frame, raw_w, raw_h)
            if ix.size > 0:
                flat_indices = (iy * raw_w) + ix
                order = _last_occurrence_order(flat_indices)
                target_indices = flat_indices[order]
                anomaly_values = anomaly_array.reshape(-1)[source_indices][order]
                gray_values = raw_array.reshape(-1)[target_indices]
                heat_rgb = _jet_colormap_array(anomaly_values)
                blended = (heat_rgb.astype(np.float32) * 0.4 + gray_values[:, None].astype(np.float32) * 0.6).astype(
                    np.uint8,
                    copy=False,
                )
                rgb.reshape(-1, 3)[target_indices] = blended

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


def _decision_color(decision: str) -> tuple[int, int, int]:
    return {
        "OK": (0, 180, 90),
        "RECHECK": (255, 190, 40),
        "NG": (255, 64, 64),
        "ERROR": (180, 80, 255),
    }.get(decision, (255, 255, 255))


def _jet_colormap(value: float) -> tuple[int, int, int]:
    """JET colormap: [0, 1] -> (R, G, B)。"""
    return tuple(int(item) for item in _jet_colormap_array(np.asarray([value], dtype=np.float32))[0])


def _jet_colormap_array(values: np.ndarray) -> np.ndarray:
    values = np.clip(np.asarray(values, dtype=np.float32), 0.0, 1.0)
    result = np.zeros((values.size, 3), dtype=np.float32)

    mask = values < 0.125
    t = values[mask] / 0.125
    result[mask, 2] = 128.0 + (255.0 - 128.0) * t

    mask = (values >= 0.125) & (values < 0.375)
    t = (values[mask] - 0.125) / 0.25
    result[mask, 1] = 255.0 * t
    result[mask, 2] = 255.0

    mask = (values >= 0.375) & (values < 0.625)
    t = (values[mask] - 0.375) / 0.25
    result[mask, 0] = 255.0 * t
    result[mask, 1] = 255.0
    result[mask, 2] = 255.0 * (1.0 - t)

    mask = (values >= 0.625) & (values < 0.875)
    t = (values[mask] - 0.625) / 0.25
    result[mask, 0] = 255.0
    result[mask, 1] = 255.0 * (1.0 - t)

    mask = values >= 0.875
    t = (values[mask] - 0.875) / 0.125
    result[mask, 0] = 255.0 + (128.0 - 255.0) * t

    return np.clip(result, 0.0, 255.0).astype(np.uint8)


def _resize_anomaly_map(
    anomaly_map: tuple[tuple[float, ...], ...],
    src_shape: tuple[int, int],
    target_h: int,
    target_w: int,
) -> list[list[float]]:
    """最近邻上采样 anomaly_map 到目标尺寸 [target_h, target_w]。"""
    src_h, src_w = src_shape
    if src_h <= 0 or src_w <= 0:
        return [[0.0 for _x in range(target_w)] for _y in range(target_h)]
    return _resize_anomaly_map_array(anomaly_map, target_h, target_w).tolist()


def _resize_anomaly_map_array(
    anomaly_map: tuple[tuple[float, ...], ...],
    target_h: int,
    target_w: int,
) -> np.ndarray:
    source = np.asarray(anomaly_map, dtype=np.float32)
    if source.ndim != 2 or source.shape[0] <= 0 or source.shape[1] <= 0 or target_h <= 0 or target_w <= 0:
        return np.zeros((max(target_h, 0), max(target_w, 0)), dtype=np.float32)
    if source.shape == (target_h, target_w):
        return source
    row_indices = np.minimum((np.arange(target_h) * source.shape[0] // target_h).astype(np.intp), source.shape[0] - 1)
    col_indices = np.minimum((np.arange(target_w) * source.shape[1] // target_w).astype(np.intp), source.shape[1] - 1)
    return source[row_indices[:, None], col_indices[None, :]]


def _last_occurrence_order(indices: np.ndarray) -> np.ndarray:
    if indices.size == 0:
        return indices.astype(np.intp, copy=False)
    reversed_indices = indices[::-1]
    _unique_values, first_reversed = np.unique(reversed_indices, return_index=True)
    last_positions = indices.size - 1 - first_reversed
    return np.sort(last_positions).astype(np.intp, copy=False)
