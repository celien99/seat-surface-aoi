from __future__ import annotations

import hashlib
import json
import math
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import Recipe
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
        self._write_roi_images(trace_dir, context.get("prepared_bundles", []))
        self._write_defect_overlays(trace_dir, result, context.get("prepared_bundles", []))
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
                    self._write_pgm(
                        trace_dir
                        / "images"
                        / _safe_name(bundle.camera_id)
                        / _safe_name(getattr(bundle, "pose_id", "") or bundle.camera_id)
                        / _safe_name(roi_name)
                        / f"{_safe_name(light_id)}.pgm",
                        frame,
                    )

    def _write_defect_overlays(self, trace_dir: Path, result: InspectionResult, prepared_bundles: Any) -> None:
        if not result.defects:
            return
        frame_index = self._frame_index(prepared_bundles)
        overlay_dir = trace_dir / "overlays"
        for defect in result.defects:
            frame = self._frame_for_defect(defect, frame_index)
            if frame is None:
                continue
            self._write_overlay_ppm(
                overlay_dir
                / (
                    f"{_safe_name(defect.defect_id)}_{_safe_name(defect.camera_id)}_"
                    f"{_safe_name(defect.pose_id or defect.camera_id)}_{_safe_name(defect.roi_name)}.ppm"
                ),
                frame,
                defect,
            )

    def _frame_index(self, prepared_bundles: Any) -> dict[tuple[str, str, str, str], LightFrame]:
        index: dict[tuple[str, str, str, str], LightFrame] = {}
        for bundle in prepared_bundles or []:
            for roi_name, frames in getattr(bundle, "rois", {}).items():
                for light_id, frame in frames.items():
                    pose_id = getattr(bundle, "pose_id", "") or bundle.camera_id
                    index[(bundle.camera_id, pose_id, roi_name, light_id)] = frame
        return index

    def _frame_for_defect(
        self,
        defect: DefectResult,
        frame_index: dict[tuple[str, str, str, str], LightFrame],
    ) -> LightFrame | None:
        pose_id = defect.pose_id or defect.camera_id
        for light_id in defect.evidence_lights:
            frame = frame_index.get((defect.camera_id, pose_id, defect.roi_name, light_id))
            if frame is not None:
                return frame
        return (
            frame_index.get((defect.camera_id, pose_id, defect.roi_name, "DIFFUSE"))
            or next(
                (
                    frame
                    for (camera_id, frame_pose_id, roi_name, _light_id), frame in frame_index.items()
                    if camera_id == defect.camera_id and frame_pose_id == pose_id and roi_name == defect.roi_name
                ),
                None,
            )
        )

    def _write_pgm(self, path: Path, frame: LightFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        header = f"P5\n{frame.width} {frame.height}\n255\n".encode("ascii")
        path.write_bytes(header + self._frame_bytes(frame))

    def _write_overlay_ppm(self, path: Path, frame: LightFrame, defect: DefectResult) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        gray = self._frame_bytes(frame)
        x0, y0, x1, y1 = self._bbox_in_frame(defect.bbox_xyxy_pixel, frame)
        rgb = bytearray()
        for y in range(frame.height):
            for x in range(frame.width):
                value = gray[y * frame.width + x]
                if x0 <= x <= x1 and y0 <= y <= y1 and (x in {x0, x1} or y in {y0, y1}):
                    rgb.extend((255, 0, 0))
                else:
                    rgb.extend((value, value, value))
        header = f"P6\n{frame.width} {frame.height}\n255\n".encode("ascii")
        path.write_bytes(header + bytes(rgb))

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

    def _bbox_in_frame(self, bbox_xyxy_pixel: tuple[int, int, int, int], frame: LightFrame) -> tuple[int, int, int, int]:
        if frame.source_to_roi_matrix is not None:
            x0, y0, x1, y1 = bbox_xyxy_pixel
            points = (
                self._apply_homography(frame.source_to_roi_matrix, float(x0), float(y0)),
                self._apply_homography(frame.source_to_roi_matrix, float(x1), float(y0)),
                self._apply_homography(frame.source_to_roi_matrix, float(x1), float(y1)),
                self._apply_homography(frame.source_to_roi_matrix, float(x0), float(y1)),
            )
            if any(point is None for point in points):
                raise ValueError(f"defect bbox 无法映射到 ROI: {bbox_xyxy_pixel}")
            xs = [point[0] for point in points if point is not None]
            ys = [point[1] for point in points if point is not None]
            local = (
                max(0, min(frame.width - 1, math.floor(min(xs)))),
                max(0, min(frame.height - 1, math.floor(min(ys)))),
                max(0, min(frame.width - 1, math.ceil(max(xs)))),
                max(0, min(frame.height - 1, math.ceil(max(ys)))),
            )
            if local[2] < local[0] or local[3] < local[1]:
                raise ValueError(f"defect bbox 不在 ROI 内: {bbox_xyxy_pixel}")
            return local
        origin_x, origin_y = frame.origin_xy
        x0, y0, x1, y1 = bbox_xyxy_pixel
        local = (
            max(0, min(frame.width - 1, x0 - origin_x)),
            max(0, min(frame.height - 1, y0 - origin_y)),
            max(0, min(frame.width - 1, x1 - origin_x)),
            max(0, min(frame.height - 1, y1 - origin_y)),
        )
        if local[2] < local[0] or local[3] < local[1]:
            raise ValueError(f"defect bbox 不在 ROI 内: {bbox_xyxy_pixel}")
        return local

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
