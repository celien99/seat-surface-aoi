from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

from python_detector.config.calibration_manager import RoiMask, RoiTemplate
from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import LightFrame
from python_detector.models.asset_errors import ModelAssetUnavailableError
from python_detector.models.onnx_runtime import create_onnx_session, numpy_module, run_first_input
from python_detector.models.yolo_decode import SegmentationCandidate, decode_yolo_rows, decode_yolo_segmentation


@dataclass(frozen=True)
class RoiInputTransform:
    width: int
    height: int
    scale: float
    pad_x: float
    pad_y: float


@dataclass(frozen=True)
class RoiLocation:
    roi_name: str
    confidence: float
    polygon_xy: tuple[tuple[int, int], ...]
    output_size: tuple[int, int]
    pose_error_px: float
    source: str


@dataclass(frozen=True)
class RuntimeRoiLocation:
    roi_name: str
    confidence: float
    polygon_xy: tuple[tuple[int, int], ...]
    output_size: tuple[int, int]
    pose_error_px: float
    source: str
    mask: RoiMask | None = None


@dataclass(frozen=True)
class RoiLocationReport:
    camera_id: str
    dome_light_id: str
    backend: str
    is_pass: bool
    message: str
    locations: tuple[RoiLocation, ...]


class RoiLocator:
    def __init__(self) -> None:
        self._onnx_sessions: dict[str, Any] = {}

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
        elif config.backend == "onnx_yolo_seg":
            detections = self._onnx_yolo_segmentation(dome_frame, recipe)
            return self._locations_from_segmentations(
                camera_id,
                dome_light_id,
                detections,
                dome_frame,
                templates,
                recipe,
            )
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
        session = self._cached_onnx_session(model_path, "YOLO ROI")
        tensor, _ = self._frame_to_nchw(dome_frame, recipe, np)
        outputs = run_first_input(session, tensor, "YOLO ROI")
        return decode_yolo_rows(
            outputs[0],
            confidence_threshold=recipe.roi_locator.min_confidence,
            output_decode=recipe.roi_locator.output_decode,
        )

    def _onnx_yolo_segmentation(self, dome_frame: LightFrame, recipe: Recipe) -> list[SegmentationCandidate]:
        model_path = recipe.roi_locator.model_path
        if not model_path:
            raise ModelAssetUnavailableError(
                "YOLO ROI segmentation 模型路径不能为空",
                asset_kind="onnx_model",
                asset_path="",
                reason="path_not_configured",
        )
        np = numpy_module("YOLO ROI segmentation")
        session = self._cached_onnx_session(model_path, "YOLO ROI segmentation")
        tensor, transform = self._frame_to_nchw(dome_frame, recipe, np)
        outputs = run_first_input(session, tensor, "YOLO ROI segmentation")
        candidates = decode_yolo_segmentation(
            outputs,
            confidence_threshold=recipe.roi_locator.min_confidence,
            mask_threshold=recipe.roi_locator.mask_threshold,
            output_decode=recipe.roi_locator.output_decode,
        )
        return [
            self._map_segmentation_candidate_from_model_input(
                candidate,
                transform,
                dome_frame,
                output_decode=recipe.roi_locator.output_decode,
            )
            for candidate in candidates
        ]

    def _frame_to_nchw(self, frame: LightFrame, recipe: Recipe, np: Any) -> tuple[Any, RoiInputTransform]:
        target_width = recipe.roi_locator.input_width or frame.width
        target_height = recipe.roi_locator.input_height or frame.height
        channels = recipe.roi_locator.input_channels
        if target_width == frame.width and target_height == frame.height:
            transform = RoiInputTransform(
                width=target_width,
                height=target_height,
                scale=1.0,
                pad_x=0.0,
                pad_y=0.0,
            )
            rows = [
                [float(value) / 255.0 for value in frame.image[y * frame.stride_bytes : y * frame.stride_bytes + frame.width]]
                for y in range(frame.height)
            ]
        else:
            rows, transform = self._letterbox_rows(frame, target_width, target_height)
        array = np.asarray(rows, dtype=np.float32)
        if channels == 1:
            return array.reshape(1, 1, target_height, target_width), transform
        stacked = np.stack([array] * channels, axis=0)
        return stacked.reshape(1, channels, target_height, target_width), transform

    def _letterbox_rows(
        self,
        frame: LightFrame,
        target_width: int,
        target_height: int,
    ) -> tuple[list[list[float]], RoiInputTransform]:
        scale = min(float(target_width) / float(frame.width), float(target_height) / float(frame.height))
        resized_width = max(1, int(round(float(frame.width) * scale)))
        resized_height = max(1, int(round(float(frame.height) * scale)))
        pad_x = (float(target_width - resized_width)) / 2.0
        pad_y = (float(target_height - resized_height)) / 2.0
        rows: list[list[float]] = []
        for y in range(target_height):
            row: list[float] = []
            source_y = (float(y) - pad_y) / scale
            sy = int(round(source_y))
            for x in range(target_width):
                source_x = (float(x) - pad_x) / scale
                sx = int(round(source_x))
                if sx < 0 or sy < 0 or sx >= frame.width or sy >= frame.height:
                    row.append(0.0)
                else:
                    row.append(float(frame.image[sy * frame.stride_bytes + sx]) / 255.0)
            rows.append(row)
        return rows, RoiInputTransform(
            width=target_width,
            height=target_height,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
        )

    def _bbox_from_model_input(
        self,
        bbox_xyxy: tuple[float, float, float, float],
        transform: RoiInputTransform,
        frame: LightFrame,
    ) -> tuple[float, float, float, float]:
        x0, y0, x1, y1 = bbox_xyxy
        return (
            max(0.0, min(float(frame.width - 1), (x0 - transform.pad_x) / transform.scale)),
            max(0.0, min(float(frame.height - 1), (y0 - transform.pad_y) / transform.scale)),
            max(0.0, min(float(frame.width - 1), (x1 - transform.pad_x) / transform.scale)),
            max(0.0, min(float(frame.height - 1), (y1 - transform.pad_y) / transform.scale)),
        )

    def _map_segmentation_candidate_from_model_input(
        self,
        candidate: SegmentationCandidate,
        transform: RoiInputTransform,
        frame: LightFrame,
        *,
        output_decode: str,
    ) -> SegmentationCandidate:
        mapped_bbox = self._bbox_from_model_input(candidate.bbox_xyxy, transform, frame)
        mapped_mask_bbox = self._bbox_from_model_input(
            candidate.mask_bbox_xyxy or candidate.bbox_xyxy,
            transform,
            frame,
        )
        mask = candidate.mask
        if output_decode == "ultralytics_yolo_seg" and self._uses_proto_canvas_bbox(candidate, transform):
            # Ultralytics seg outputs a proto-canvas mask. The valid mask region must be
            # cropped to the detection bbox before mapping back to the source image.
            mask = self._crop_mask_to_canvas_bbox(
                candidate.mask,
                candidate.bbox_xyxy,
                canvas_width=transform.width,
                canvas_height=transform.height,
            )
            mapped_mask_bbox = mapped_bbox
        return SegmentationCandidate(
            bbox_xyxy=mapped_bbox,
            score=candidate.score,
            class_id=candidate.class_id,
            mask=mask,
            mask_bbox_xyxy=mapped_mask_bbox,
        )

    def _uses_proto_canvas_bbox(
        self,
        candidate: SegmentationCandidate,
        transform: RoiInputTransform,
    ) -> bool:
        mask_shape = getattr(candidate.mask, "shape", ())
        if len(mask_shape) < 2 or candidate.mask_bbox_xyxy is None:
            return False
        mask_height = int(mask_shape[0])
        mask_width = int(mask_shape[1])
        if mask_width <= 0 or mask_height <= 0:
            return False
        if mask_width == transform.width and mask_height == transform.height:
            return False
        x0, y0, x1, y1 = candidate.mask_bbox_xyxy
        return (
            abs(x0) <= 1e-6
            and abs(y0) <= 1e-6
            and abs(x1 - float(mask_width - 1)) <= 1e-6
            and abs(y1 - float(mask_height - 1)) <= 1e-6
        )

    def _crop_mask_to_canvas_bbox(
        self,
        mask: Any,
        bbox_xyxy: tuple[float, float, float, float],
        *,
        canvas_width: int,
        canvas_height: int,
    ) -> Any:
        mask_shape = getattr(mask, "shape", ())
        if len(mask_shape) < 2:
            return mask
        mask_height = int(mask_shape[0])
        mask_width = int(mask_shape[1])
        if mask_width <= 0 or mask_height <= 0 or canvas_width <= 0 or canvas_height <= 0:
            return mask
        x0, y0, x1, y1 = bbox_xyxy
        clamped_x0 = max(0.0, min(float(canvas_width - 1), x0))
        clamped_y0 = max(0.0, min(float(canvas_height - 1), y0))
        clamped_x1 = max(clamped_x0, min(float(canvas_width - 1), x1))
        clamped_y1 = max(clamped_y0, min(float(canvas_height - 1), y1))
        mask_x0 = max(0, min(mask_width - 1, int(math.floor(clamped_x0 * mask_width / float(canvas_width)))))
        mask_y0 = max(0, min(mask_height - 1, int(math.floor(clamped_y0 * mask_height / float(canvas_height)))))
        mask_x1 = max(
            mask_x0,
            min(
                mask_width - 1,
                int(math.ceil((clamped_x1 + 1.0) * mask_width / float(canvas_width))) - 1,
            ),
        )
        mask_y1 = max(
            mask_y0,
            min(
                mask_height - 1,
                int(math.ceil((clamped_y1 + 1.0) * mask_height / float(canvas_height))) - 1,
            ),
        )
        return mask[mask_y0 : mask_y1 + 1, mask_x0 : mask_x1 + 1]

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

    def _locations_from_segmentations(
        self,
        camera_id: str,
        dome_light_id: str,
        candidates: list[SegmentationCandidate],
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
        candidates_by_roi: dict[str, list[RuntimeRoiLocation]] = {}
        for candidate in candidates:
            try:
                location = self._location_from_segmentation(candidate, dome_frame, by_class_id, recipe)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if location.confidence < recipe.roi_locator.min_confidence:
                continue
            if location.pose_error_px > recipe.roi_locator.max_pose_error_px:
                errors.append(
                    f"{location.roi_name}: mask boundary error {location.pose_error_px:.3f}px exceeds "
                    f"{recipe.roi_locator.max_pose_error_px:.3f}px"
                )
                continue
            candidates_by_roi.setdefault(location.roi_name, []).append(location)

        for roi_name, roi_candidates in candidates_by_roi.items():
            roi_candidates.sort(key=lambda item: (-item.confidence, item.pose_error_px))
            best = roi_candidates[0]
            conflicting = [
                candidate
                for candidate in roi_candidates[1:]
                if self._bbox_iou(self._bbox(candidate.polygon_xy), self._bbox(best.polygon_xy)) < 0.9
            ]
            if conflicting:
                errors.append(f"{roi_name}: duplicate conflicting ROI segmentations")
            locations.append(
                RoiLocation(
                    roi_name=best.roi_name,
                    confidence=best.confidence,
                    polygon_xy=best.polygon_xy,
                    output_size=best.output_size,
                    pose_error_px=best.pose_error_px,
                    source=best.source,
                )
            )
            located_templates[roi_name] = RoiTemplate(
                roi_name=best.roi_name,
                polygon_xy=best.polygon_xy,
                output_size=best.output_size,
                mask=best.mask,
            )

        missing = [roi_name for roi_name in templates if roi_name not in located_templates]
        is_pass = not missing and not errors and bool(located_templates)
        message = "Dome YOLO segmentation ROI pass" if is_pass else "; ".join(
            errors + [f"missing ROI segmentations: {missing}"]
        )
        return located_templates, RoiLocationReport(
            camera_id=camera_id,
            dome_light_id=dome_light_id,
            backend=recipe.roi_locator.backend,
            is_pass=is_pass,
            message=message,
            locations=tuple(locations),
        )

    def _location_from_segmentation(
        self,
        candidate: SegmentationCandidate,
        dome_frame: LightFrame,
        by_class_id: dict[int, RoiTemplate],
        recipe: Recipe,
    ) -> RuntimeRoiLocation:
        if candidate.score < 0.0 or candidate.score > 1.0:
            raise ValueError(f"YOLO segmentation confidence 越界: {candidate.score}")
        template = by_class_id.get(candidate.class_id)
        if template is None:
            raise ValueError(f"YOLO segmentation class_id 未映射到模板: {candidate.class_id}")
        mask_bbox = self._mask_bbox(candidate.mask)
        if mask_bbox is None:
            raise ValueError(f"{template.roi_name}: segmentation mask 为空")
        mask_width = int(getattr(candidate.mask, "shape", (0, 0))[1])
        mask_height = int(getattr(candidate.mask, "shape", (0, 0))[0])
        if mask_width <= 0 or mask_height <= 0:
            raise ValueError(f"{template.roi_name}: segmentation mask 尺寸无效")
        x0, y0, x1, y1 = mask_bbox
        bbox_x0, bbox_y0, bbox_x1, bbox_y1 = candidate.mask_bbox_xyxy or candidate.bbox_xyxy
        bbox_width = max(1.0, bbox_x1 - bbox_x0 + 1.0)
        bbox_height = max(1.0, bbox_y1 - bbox_y0 + 1.0)
        scale_x = bbox_width / float(mask_width)
        scale_y = bbox_height / float(mask_height)
        polygon = (
            (int(round(bbox_x0 + x0 * scale_x)), int(round(bbox_y0 + y0 * scale_y))),
            (int(round(bbox_x0 + (x1 + 1) * scale_x)) - 1, int(round(bbox_y0 + y0 * scale_y))),
            (
                int(round(bbox_x0 + (x1 + 1) * scale_x)) - 1,
                int(round(bbox_y0 + (y1 + 1) * scale_y)) - 1,
            ),
            (int(round(bbox_x0 + x0 * scale_x)), int(round(bbox_y0 + (y1 + 1) * scale_y)) - 1),
        )
        polygon = tuple(
            (
                max(0, min(dome_frame.width - 1, x)),
                max(0, min(dome_frame.height - 1, y)),
            )
            for x, y in polygon
        )
        mask_area = self._mask_area(candidate.mask)
        scaled_area = mask_area * scale_x * scale_y
        max_area = float(dome_frame.width * dome_frame.height) * recipe.roi_locator.max_mask_area_ratio
        if scaled_area < recipe.roi_locator.min_mask_area_px:
            raise ValueError(
                f"{template.roi_name}: mask area {scaled_area:.1f}px below "
                f"{recipe.roi_locator.min_mask_area_px}px"
            )
        if scaled_area > max_area:
            raise ValueError(
                f"{template.roi_name}: mask area ratio exceeds {recipe.roi_locator.max_mask_area_ratio:.3f}"
            )
        pose_error = self._safety_boundary_error_px(template, polygon)
        native_output_size = self._bbox_output_size(polygon)
        roi_mask = self._mask_to_roi_mask(candidate.mask, mask_bbox, native_output_size)
        return RuntimeRoiLocation(
            roi_name=template.roi_name,
            confidence=candidate.score,
            polygon_xy=polygon,
            output_size=native_output_size,
            pose_error_px=pose_error,
            source=recipe.roi_locator.backend,
            mask=roi_mask,
        )

    def _mask_bbox(self, mask: Any) -> tuple[int, int, int, int] | None:
        height = int(getattr(mask, "shape", (0, 0))[0])
        width = int(getattr(mask, "shape", (0, 0))[1]) if len(getattr(mask, "shape", ())) >= 2 else 0
        if width <= 0 or height <= 0:
            return None
        x0 = width
        y0 = height
        x1 = -1
        y1 = -1
        for y in range(height):
            for x in range(width):
                if float(mask[y][x]) > 0.0:
                    x0 = min(x0, x)
                    y0 = min(y0, y)
                    x1 = max(x1, x)
                    y1 = max(y1, y)
        if x1 < x0 or y1 < y0:
            return None
        return x0, y0, x1, y1

    def _mask_area(self, mask: Any) -> int:
        height = int(getattr(mask, "shape", (0, 0))[0])
        width = int(getattr(mask, "shape", (0, 0))[1]) if len(getattr(mask, "shape", ())) >= 2 else 0
        area = 0
        for y in range(height):
            for x in range(width):
                if float(mask[y][x]) > 0.0:
                    area += 1
        return area

    def _mask_to_roi_mask(
        self,
        mask: Any,
        mask_bbox: tuple[int, int, int, int],
        output_size: tuple[int, int],
    ) -> RoiMask:
        output_width, output_height = output_size
        x0, y0, x1, y1 = mask_bbox
        bbox_width = max(1, x1 - x0 + 1)
        bbox_height = max(1, y1 - y0 + 1)
        # 先上采样到输出尺寸，再在输出空间做 1px 侵蚀
        pixels = bytearray(output_width * output_height)
        for y in range(output_height):
            source_y = y0 + min(bbox_height - 1, int(float(y) * float(bbox_height) / float(output_height)))
            for x in range(output_width):
                source_x = x0 + min(bbox_width - 1, int(float(x) * float(bbox_width) / float(output_width)))
                if float(mask[source_y][source_x]) > 0.0:
                    pixels[y * output_width + x] = 255
        # 在输出空间做 1px 4-邻域侵蚀
        eroded = _erode_output_mask_1px(pixels, output_width, output_height)
        return RoiMask(width=output_width, height=output_height, pixels=bytes(eroded))

    def _bbox_pose_error_px(self, template: RoiTemplate, polygon: tuple[tuple[int, int], ...]) -> float:
        template_bbox = self._bbox(template.polygon_xy)
        located_bbox = self._bbox(polygon)
        deltas = [float(a - b) for a, b in zip(template_bbox, located_bbox)]
        return max(abs(delta) for delta in deltas)

    def _safety_boundary_error_px(self, template: RoiTemplate, polygon: tuple[tuple[int, int], ...]) -> float:
        tx0, ty0, tx1, ty1 = self._bbox(template.polygon_xy)
        x0, y0, x1, y1 = self._bbox(polygon)
        return max(
            float(tx0 - x0),
            float(ty0 - y0),
            float(x1 - tx1),
            float(y1 - ty1),
            0.0,
        )

    def _bbox(self, polygon: tuple[tuple[int, int], ...]) -> tuple[int, int, int, int]:
        xs = [point[0] for point in polygon]
        ys = [point[1] for point in polygon]
        return (min(xs), min(ys), max(xs), max(ys))

    def _bbox_output_size(self, polygon: tuple[tuple[int, int], ...]) -> tuple[int, int]:
        x0, y0, x1, y1 = self._bbox(polygon)
        return (x1 - x0 + 1, y1 - y0 + 1)

    def _bbox_iou(
        self,
        bbox_a: tuple[int, int, int, int],
        bbox_b: tuple[int, int, int, int],
    ) -> float:
        ax0, ay0, ax1, ay1 = bbox_a
        bx0, by0, bx1, by1 = bbox_b
        inter_x0 = max(ax0, bx0)
        inter_y0 = max(ay0, by0)
        inter_x1 = min(ax1, bx1)
        inter_y1 = min(ay1, by1)
        if inter_x1 < inter_x0 or inter_y1 < inter_y0:
            return 0.0
        intersection = float((inter_x1 - inter_x0 + 1) * (inter_y1 - inter_y0 + 1))
        area_a = float((ax1 - ax0 + 1) * (ay1 - ay0 + 1))
        area_b = float((bx1 - bx0 + 1) * (by1 - by0 + 1))
        denominator = area_a + area_b - intersection
        if denominator <= 0.0:
            return 0.0
        return intersection / denominator

    def _cached_onnx_session(self, model_path: str, label: str) -> Any:
        if model_path not in self._onnx_sessions:
            self._onnx_sessions[model_path] = create_onnx_session(model_path, label)
        return self._onnx_sessions[model_path]


def _erode_output_mask_1px(pixels: bytearray, width: int, height: int) -> bytearray:
    """在输出空间对 mask 做 1 像素 4-邻域腐蚀，消除分割边界锯齿。"""
    eroded = bytearray(pixels)
    for y in range(height):
        for x in range(width):
            idx = y * width + x
            if pixels[idx] == 0:
                continue
            if (
                (x == 0 or pixels[idx - 1] == 0)
                or (x == width - 1 or pixels[idx + 1] == 0)
                or (y == 0 or pixels[idx - width] == 0)
                or (y == height - 1 or pixels[idx + width] == 0)
            ):
                eroded[idx] = 0
    return eroded
