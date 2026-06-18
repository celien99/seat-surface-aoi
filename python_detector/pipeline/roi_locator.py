from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

from python_detector.config.calibration_manager import RoiTemplate
from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import LightFrame
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.onnx_runtime import create_onnx_session, numpy_module, run_first_input
from python_detector.models.yolo_decode import decode_yolo_rows


@dataclass(frozen=True)
class RoiLocation:
    roi_name: str
    confidence: float
    polygon_xy: tuple[tuple[int, int], ...]
    output_size: tuple[int, int]
    pose_error_px: float
    source: str


@dataclass(frozen=True)
class RoiLocationReport:
    camera_id: str
    dome_light_id: str
    backend: str
    is_pass: bool
    message: str
    locations: tuple[RoiLocation, ...]


class RoiLocator:
    def locate(
        self,
        camera_id: str,
        frames: dict[str, LightFrame],
        templates: dict[str, RoiTemplate],
        recipe: Recipe,
    ) -> tuple[dict[str, RoiTemplate], RoiLocationReport]:
        config = recipe.roi_locator
        dome_light_id = recipe.semantic_light_id(config.dome_semantic_light)
        dome_frame = frames.get(dome_light_id)
        if dome_frame is None:
            return {}, RoiLocationReport(
                camera_id=camera_id,
                dome_light_id=dome_light_id,
                backend=config.backend,
                is_pass=False,
                message=f"missing Dome ROI source light: {dome_light_id}",
                locations=(),
            )
        if config.backend == "template":
            return self._template_locations(camera_id, dome_light_id, templates, recipe)
        if config.backend == "fake_yolo":
            detections = self._fake_yolo_rows(templates, recipe)
        elif config.backend == "onnx_yolo":
            detections = self._onnx_yolo_rows(dome_frame, recipe)
        else:
            raise ValueError(f"不支持的 ROI 定位后端: {config.backend}")
        return self._locations_from_detections(camera_id, dome_light_id, detections, dome_frame, templates, recipe)

    def _template_locations(
        self,
        camera_id: str,
        dome_light_id: str,
        templates: dict[str, RoiTemplate],
        recipe: Recipe,
    ) -> tuple[dict[str, RoiTemplate], RoiLocationReport]:
        locations = tuple(
            RoiLocation(
                roi_name=template.roi_name,
                confidence=1.0,
                polygon_xy=template.polygon_xy,
                output_size=template.output_size,
                pose_error_px=0.0,
                source="template",
            )
            for template in templates.values()
        )
        return templates, RoiLocationReport(
            camera_id=camera_id,
            dome_light_id=dome_light_id,
            backend=recipe.roi_locator.backend,
            is_pass=True,
            message="template ROI pass",
            locations=locations,
        )

    def _fake_yolo_rows(self, templates: dict[str, RoiTemplate], recipe: Recipe) -> list[list[float]]:
        rows: list[list[float]] = []
        class_ids = {roi_name: index for index, roi_name in enumerate(recipe.roi_locator.class_names)}
        for template in templates.values():
            if template.roi_name not in class_ids:
                continue
            class_id = class_ids[template.roi_name]
            x_values = [point[0] for point in template.polygon_xy]
            y_values = [point[1] for point in template.polygon_xy]
            rows.append([float(min(x_values)), float(min(y_values)), float(max(x_values)), float(max(y_values)), 0.99, float(class_id)])
        return rows

    def _onnx_yolo_rows(self, dome_frame: LightFrame, recipe: Recipe) -> list[list[float]]:
        model_path = recipe.roi_locator.model_path
        if not model_path:
            raise ModelAssetUnavailableError(
                "YOLO ROI 模型路径不能为空",
                asset_kind="onnx_model",
                asset_path="",
                reason="path_not_configured",
            )
        np = numpy_module("YOLO ROI")
        session = create_onnx_session(model_path, "YOLO ROI")
        tensor = self._frame_to_nchw(dome_frame, np)
        outputs = run_first_input(session, tensor, "YOLO ROI")
        return decode_yolo_rows(
            outputs[0],
            confidence_threshold=recipe.roi_locator.min_confidence,
            output_decode=recipe.roi_locator.output_decode,
        )

    def _frame_to_nchw(self, frame: LightFrame, np: Any) -> Any:
        rows = []
        for y in range(frame.height):
            start = y * frame.stride_bytes
            rows.append([float(value) / 255.0 for value in frame.image[start : start + frame.width]])
        array = np.asarray(rows, dtype=np.float32)
        return array.reshape(1, 1, frame.height, frame.width)

    def _locations_from_detections(
        self,
        camera_id: str,
        dome_light_id: str,
        rows: list[list[float]],
        dome_frame: LightFrame,
        templates: dict[str, RoiTemplate],
        recipe: Recipe,
    ) -> tuple[dict[str, RoiTemplate], RoiLocationReport]:
        by_class_id = {
            index: templates[roi_name]
            for index, roi_name in enumerate(recipe.roi_locator.class_names)
            if roi_name in templates
        }
        locations: list[RoiLocation] = []
        located_templates: dict[str, RoiTemplate] = {}
        errors: list[str] = []
        candidates_by_roi: dict[str, list[RoiLocation]] = {}
        for row in rows:
            try:
                location = self._location_from_row(row, dome_frame, by_class_id, recipe)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if location.confidence < recipe.roi_locator.min_confidence:
                continue
            if location.pose_error_px > recipe.roi_locator.max_pose_error_px:
                errors.append(
                    f"{location.roi_name}: pose error {location.pose_error_px:.3f}px exceeds "
                    f"{recipe.roi_locator.max_pose_error_px:.3f}px"
                )
                continue
            candidates_by_roi.setdefault(location.roi_name, []).append(location)

        for roi_name, candidates in candidates_by_roi.items():
            candidates.sort(key=lambda item: (-item.confidence, item.pose_error_px))
            best = candidates[0]
            conflicting = [candidate for candidate in candidates[1:] if candidate.polygon_xy != best.polygon_xy]
            if conflicting:
                errors.append(f"{roi_name}: duplicate conflicting ROI detections")
            locations.append(best)
            located_templates[roi_name] = RoiTemplate(
                roi_name=best.roi_name,
                polygon_xy=best.polygon_xy,
                output_size=best.output_size,
            )

        missing = [roi_name for roi_name in templates if roi_name not in located_templates]
        is_pass = not missing and not errors and bool(located_templates)
        message = "Dome YOLO ROI pass" if is_pass else "; ".join(errors + [f"missing ROI detections: {missing}"])
        return located_templates, RoiLocationReport(
            camera_id=camera_id,
            dome_light_id=dome_light_id,
            backend=recipe.roi_locator.backend,
            is_pass=is_pass,
            message=message,
            locations=tuple(locations),
        )

    def _location_from_row(
        self,
        row: list[float],
        dome_frame: LightFrame,
        by_class_id: dict[int, RoiTemplate],
        recipe: Recipe,
    ) -> RoiLocation:
        if len(row) < 6:
            raise ValueError("YOLO ROI row length < 6")
        x0, y0, x1, y1, confidence, class_value = (float(value) for value in row[:6])
        if not all(math.isfinite(value) for value in (x0, y0, x1, y1, confidence, class_value)):
            raise ValueError(f"YOLO ROI row 包含非有限值: {row[:6]}")
        if confidence < 0.0 or confidence > 1.0:
            raise ValueError(f"YOLO ROI confidence 越界: {confidence}")
        if not class_value.is_integer():
            raise ValueError(f"YOLO ROI class_id 不是整数: {class_value}")
        class_id = int(class_value)
        template = by_class_id.get(class_id)
        if template is None:
            raise ValueError(f"YOLO ROI class_id 未映射到模板: {class_id}")
        if recipe.roi_locator.bbox_format == "xyxy_normalized":
            if not all(0.0 <= value <= 1.0 for value in (x0, y0, x1, y1)):
                raise ValueError(f"YOLO ROI 归一化 bbox 越界: {(x0, y0, x1, y1)}")
            x0 *= float(dome_frame.width - 1)
            x1 *= float(dome_frame.width - 1)
            y0 *= float(dome_frame.height - 1)
            y1 *= float(dome_frame.height - 1)
        elif recipe.roi_locator.bbox_format != "xyxy_pixel":
            raise ValueError(f"不支持的 YOLO ROI bbox_format: {recipe.roi_locator.bbox_format}")
        if x1 < x0 or y1 < y0:
            raise ValueError(f"YOLO ROI bbox 坐标反向: {(x0, y0, x1, y1)}")
        if x0 < 0 or y0 < 0 or x1 > dome_frame.width - 1 or y1 > dome_frame.height - 1:
            raise ValueError(f"YOLO ROI bbox 越界: {(x0, y0, x1, y1)}")
        polygon = (
            (int(round(x0)), int(round(y0))),
            (int(round(x1)), int(round(y0))),
            (int(round(x1)), int(round(y1))),
            (int(round(x0)), int(round(y1))),
        )
        pose_error = self._bbox_pose_error_px(template, polygon)
        return RoiLocation(
            roi_name=template.roi_name,
            confidence=confidence,
            polygon_xy=polygon,
            output_size=template.output_size,
            pose_error_px=pose_error,
            source=recipe.roi_locator.backend,
        )

    def _bbox_pose_error_px(self, template: RoiTemplate, polygon: tuple[tuple[int, int], ...]) -> float:
        template_bbox = self._bbox(template.polygon_xy)
        located_bbox = self._bbox(polygon)
        deltas = [float(a - b) for a, b in zip(template_bbox, located_bbox)]
        return max(abs(delta) for delta in deltas)

    def _bbox(self, polygon: tuple[tuple[int, int], ...]) -> tuple[int, int, int, int]:
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        return (min(xs), min(ys), max(xs), max(ys))
