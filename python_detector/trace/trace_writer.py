from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

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
            # 查找匹配的 raw 帧
            raw_frame = raw_index.get((camera_id, pose_id, roi_frame.light_id))
            if raw_frame is None:
                # fallback: 同 camera/pose 的任意光源
                for (rc, rp, _rl), rf in raw_index.items():
                    if rc == camera_id and rp == pose_id:
                        raw_frame = rf
                        break
            if raw_frame is None:
                continue
            anomaly_entry = anomaly_maps.get(key)
            path = overlay_dir / _safe_name(camera_id) / _safe_name(pose_id) / f"{_safe_name(roi_name)}.png"
            if anomaly_entry is not None:
                anomaly_map, _spatial_shape = anomaly_entry
                self._write_heatmap_overlay_on_raw(
                    path, raw_frame, roi_frame, result.decision, roi_defects, anomaly_map,
                )
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
        """仅绘制 raw 底图 + 决策边框 + 缺陷 bbox（无热力图）。"""
        raw_bytes = self._frame_bytes(raw_frame)
        raw_w, raw_h = raw_frame.width, raw_frame.height
        rgb = bytearray(raw_w * raw_h * 3)
        for i in range(raw_w * raw_h):
            gray_val = raw_bytes[i]
            off = i * 3
            rgb[off] = gray_val
            rgb[off + 1] = gray_val
            rgb[off + 2] = gray_val
        self._draw_overlay_marks_raw(rgb, raw_w, raw_h, result_decision, defects)
        write_rgb_png(path, raw_w, raw_h, bytes(rgb))

    def _anomaly_map_index(
        self,
        spatial_maps: list[dict[str, object]],
    ) -> dict[tuple[str, str, str], tuple[tuple[tuple[float, ...], ...], tuple[int, int]]]:
        """从 spatial_maps 中提取 anomaly_map 索引。

        返回 {(camera_id, pose_id, roi_name): (anomaly_map, spatial_shape)}。
        """
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
                anomaly_map_tuple = tuple(
                    tuple(float(v) for v in row) for row in anomaly_map
                )
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
        """渲染 JET 热力图叠加到 raw 原图上，并绘制判定框和缺陷框。

        通过 roi_to_source_matrix 将 ROI 空间的热力图前向映射到 raw 坐标；
        defect bbox 本身就是 raw 坐标，直接绘制。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        raw_bytes = self._frame_bytes(raw_frame)
        raw_w, raw_h = raw_frame.width, raw_frame.height
        roi_w, roi_h = roi_frame.width, roi_frame.height
        matrix = roi_frame.roi_to_source_matrix

        # 上采样 anomaly_map 到 ROI 分辨率
        map_h = len(anomaly_map)
        map_w = len(anomaly_map[0]) if map_h > 0 else 0
        upsampled = _resize_anomaly_map(anomaly_map, (map_h, map_w), roi_h, roi_w)

        # 构建 RGB 底图 = raw 灰度三通道
        rgb = bytearray(raw_w * raw_h * 3)
        for i in range(raw_w * raw_h):
            gray_val = raw_bytes[i]
            off = i * 3
            rgb[off] = gray_val
            rgb[off + 1] = gray_val
            rgb[off + 2] = gray_val

        # 前向映射：ROI 像素 → raw 坐标 → 混合 JET 热力图
        has_matrix = matrix is not None and len(matrix) == 9
        for ry in range(roi_h):
            for rx in range(roi_w):
                if has_matrix:
                    mapped = self._apply_homography(matrix, float(rx), float(ry))
                    if mapped is None:
                        continue
                    sx, sy = mapped
                else:
                    ox, oy = roi_frame.origin_xy
                    sx = float(rx + ox)
                    sy = float(ry + oy)

                ix = int(round(sx))
                iy = int(round(sy))
                if ix < 0 or ix >= raw_w or iy < 0 or iy >= raw_h:
                    continue

                anomaly_val = upsampled[ry][rx]
                gray_val = raw_bytes[iy * raw_w + ix]
                heat_r, heat_g, heat_b = _jet_colormap(anomaly_val)
                blended_r = int(heat_r * 0.4 + gray_val * 0.6)
                blended_g = int(heat_g * 0.4 + gray_val * 0.6)
                blended_b = int(heat_b * 0.4 + gray_val * 0.6)

                base = (iy * raw_w + ix) * 3
                rgb[base] = blended_r
                rgb[base + 1] = blended_g
                rgb[base + 2] = blended_b

        self._draw_overlay_marks_raw(rgb, raw_w, raw_h, result_decision, defects)
        write_rgb_png(path, raw_w, raw_h, bytes(rgb))

    def _draw_overlay_marks_raw(
        self,
        rgb: bytearray,
        width: int,
        height: int,
        result_decision: str,
        defects: list[DefectResult],
    ) -> None:
        """在 raw 图像上绘制决策边框和缺陷 bbox。

        DefectResult.bbox_xyxy_pixel 本身就是源（raw）坐标，无需转换。
        """
        self._draw_rect(rgb, width, height, 0, 0, width - 1, height - 1, _decision_color(result_decision), thickness=4)
        for defect in defects:
            x0, y0, x1, y1 = defect.bbox_xyxy_pixel
            self._draw_rect(rgb, width, height, x0, y0, x1, y1, _decision_color(defect.decision), thickness=3)

    def _draw_rect(
        self,
        rgb: bytearray,
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
        for offset in range(thickness):
            self._draw_hline(rgb, width, height, x0, x1, y0 + offset, color)
            self._draw_hline(rgb, width, height, x0, x1, y1 - offset, color)
            self._draw_vline(rgb, width, height, x0 + offset, y0, y1, color)
            self._draw_vline(rgb, width, height, x1 - offset, y0, y1, color)

    def _draw_hline(
        self,
        rgb: bytearray,
        width: int,
        height: int,
        x0: int,
        x1: int,
        y: int,
        color: tuple[int, int, int],
    ) -> None:
        if y < 0 or y >= height:
            return
        for x in range(max(0, x0), min(width - 1, x1) + 1):
            self._set_pixel(rgb, width, x, y, color)

    def _draw_vline(
        self,
        rgb: bytearray,
        width: int,
        height: int,
        x: int,
        y0: int,
        y1: int,
        color: tuple[int, int, int],
    ) -> None:
        if x < 0 or x >= width:
            return
        for y in range(max(0, y0), min(height - 1, y1) + 1):
            self._set_pixel(rgb, width, x, y, color)

    def _set_pixel(self, rgb: bytearray, width: int, x: int, y: int, color: tuple[int, int, int]) -> None:
        index = (y * width + x) * 3
        rgb[index : index + 3] = bytes(color)

    def _frame_bytes(self, frame: LightFrame) -> bytes:
        if frame.dtype != "UINT8" or frame.channels != 1:
            raise ValueError(f"trace 仅支持 UINT8 MONO ROI: {frame.camera_id}/{frame.light_id}")
        expected = frame.stride_bytes * frame.height
        if len(frame.image) < expected:
            raise ValueError(f"trace ROI 图像长度不足: {frame.camera_id}/{frame.light_id}")
        if frame.stride_bytes == frame.width:
            return bytes(frame.image[: frame.width * frame.height])
        rows = bytearray()
        for row in range(frame.height):
            start = row * frame.stride_bytes
            rows.extend(frame.image[start : start + frame.width])
        return bytes(rows)

    def _apply_homography(self, matrix: tuple[float, ...], x: float, y: float) -> tuple[float, float] | None:
        denom = matrix[6] * x + matrix[7] * y + matrix[8]
        if abs(denom) < 1e-9:
            return None
        mapped_x = (matrix[0] * x + matrix[1] * y + matrix[2]) / denom
        mapped_y = (matrix[3] * x + matrix[4] * y + matrix[5]) / denom
        return mapped_x, mapped_y


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
    """JET colormap: [0, 1] → (R, G, B)。"""

    def _clamp(v: float) -> float:
        return max(0.0, min(1.0, v))

    def _lerp(a: float, b: float, t: float) -> float:
        return a + (b - a) * t

    # JET 的关键控制点
    if value < 0.125:
        t = value / 0.125
        return (0, 0, int(_lerp(128, 255, t)))
    if value < 0.375:
        t = (value - 0.125) / 0.25
        return (0, int(_lerp(0, 255, t)), 255)
    if value < 0.625:
        t = (value - 0.375) / 0.25
        return (int(_lerp(0, 255, t)), 255, int(_lerp(255, 0, t)))
    if value < 0.875:
        t = (value - 0.625) / 0.25
        return (255, int(_lerp(255, 0, t)), 0)
    t = (value - 0.875) / 0.125
    return (int(_lerp(255, 128, t)), 0, 0)


def _resize_anomaly_map(
    anomaly_map: tuple[tuple[float, ...], ...],
    src_shape: tuple[int, int],
    target_h: int,
    target_w: int,
) -> list[list[float]]:
    """最近邻上采样 anomaly_map 到目标尺寸 [target_h, target_w]."""
    src_h, src_w = src_shape
    if src_h == target_h and src_w == target_w:
        return [list(row) for row in anomaly_map]
    result: list[list[float]] = []
    for y in range(target_h):
        src_y = min(int(y * src_h / target_h), src_h - 1)
        row: list[float] = []
        for x in range(target_w):
            src_x = min(int(x * src_w / target_w), src_w - 1)
            row.append(anomaly_map[src_y][src_x])
        result.append(row)
    return result
