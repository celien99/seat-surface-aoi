from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

import yaml

from python_detector.config.recipe_schema import RecipeValidationError
from python_detector.paths import PACKAGE_ROOT, resolve_package_path


@dataclass(frozen=True)
class RoiMask:
    width: int
    height: int
    pixels: bytes


@dataclass(frozen=True)
class RoiTemplate:
    roi_name: str
    polygon_xy: tuple[tuple[int, int], ...]
    output_size: tuple[int, int]
    mask: RoiMask | None = None


@dataclass(frozen=True)
class Calibration:
    calibration_id: str
    camera_id: str
    image_size: tuple[int, int]
    base_light_id: str
    light_alignment: dict[str, tuple[float, ...]]
    roi_templates: dict[str, RoiTemplate]
    pixel_size_mm: float | None = None


class CalibrationManager:
    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir) if base_dir is not None else PACKAGE_ROOT
        self._cache: dict[tuple[str, str, str], Calibration] = {}

    def load(self, camera_id: str, calibration_id: str, roi_template_path: str) -> Calibration:
        roi_path = resolve_package_path(self.base_dir, roi_template_path)
        key = (camera_id, calibration_id, str(roi_path))
        if key in self._cache:
            return self._cache[key]
        path = self._resolve_calibration_path(camera_id, calibration_id)
        if not path.exists():
            raise RecipeValidationError(f"标定文件不存在: {path}")
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RecipeValidationError(f"标定文件格式错误: {path}")
        calibration = self._parse_calibration(raw, camera_id, calibration_id)
        if not roi_path.exists():
            raise RecipeValidationError(f"ROI 模板文件不存在: {roi_path}")
        calibration = self._with_roi_override(calibration, roi_path)
        self._cache[key] = calibration
        return calibration

    def _resolve_calibration_path(self, camera_id: str, calibration_id: str) -> Path:
        filename = calibration_id.split("/")[-1]
        if not filename.endswith(".yaml"):
            filename = f"{filename}.yaml"
        return resolve_package_path(self.base_dir, Path("python_detector") / "config" / "calibration" / camera_id / filename)

    def _parse_calibration(self, raw: dict[str, Any], camera_id: str, calibration_id: str) -> Calibration:
        actual_camera_id = _str(raw.get("camera_id"), "camera_id")
        actual_calibration_id = _str(raw.get("calibration_id"), "calibration_id")
        if actual_camera_id != camera_id:
            raise RecipeValidationError(f"标定 camera_id 不一致: {actual_camera_id} != {camera_id}")
        if actual_calibration_id != calibration_id:
            raise RecipeValidationError(f"标定 calibration_id 不一致: {actual_calibration_id} != {calibration_id}")
        image_size = _dict(raw.get("image_size"), "image_size")
        light_alignment = _parse_light_alignment(_dict(raw.get("light_alignment", {}), "light_alignment"))
        rois = _parse_rois(_dict(raw.get("roi_templates", {}), "roi_templates"))
        return Calibration(
            calibration_id=actual_calibration_id,
            camera_id=actual_camera_id,
            image_size=(int(image_size.get("width", 0)), int(image_size.get("height", 0))),
            pixel_size_mm=None if raw.get("pixel_size_mm") is None else float(raw["pixel_size_mm"]),
            base_light_id=_str(raw.get("base_light_id", "POLAR_DIFFUSE"), "base_light_id"),
            light_alignment=light_alignment,
            roi_templates=rois,
        )

    def _with_roi_override(self, calibration: Calibration, roi_path: Path) -> Calibration:
        raw = yaml.safe_load(roi_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise RecipeValidationError(f"ROI 模板格式错误: {roi_path}")
        rois = _parse_rois(_dict(raw.get("roi_templates", raw), "roi_templates"))
        return Calibration(
            calibration_id=calibration.calibration_id,
            camera_id=calibration.camera_id,
            image_size=calibration.image_size,
            pixel_size_mm=calibration.pixel_size_mm,
            base_light_id=calibration.base_light_id,
            light_alignment=calibration.light_alignment,
            roi_templates=rois,
        )


def _parse_rois(raw: dict[str, Any]) -> dict[str, RoiTemplate]:
    rois: dict[str, RoiTemplate] = {}
    for roi_name, item in raw.items():
        item = _dict(item, f"roi_templates.{roi_name}")
        polygon = _parse_polygon(item.get("polygon_xy", []), f"roi_templates.{roi_name}.polygon_xy")
        if len(polygon) < 3:
            raise RecipeValidationError(f"ROI 至少需要 3 个点: {roi_name}")
        output_size_raw = item.get("output_size", [0, 0])
        if not isinstance(output_size_raw, list) or len(output_size_raw) != 2:
            raise RecipeValidationError(f"ROI output_size 必须是两个整数: {roi_name}")
        output_size = (
            _positive_int(output_size_raw[0], f"roi_templates.{roi_name}.output_size.width"),
            _positive_int(output_size_raw[1], f"roi_templates.{roi_name}.output_size.height"),
        )
        _validate_roi_polygon(str(roi_name), polygon)
        rois[str(roi_name)] = RoiTemplate(
            roi_name=str(roi_name),
            polygon_xy=polygon,
            output_size=output_size,
            mask=None,
        )
    if not rois:
        raise RecipeValidationError("至少需要一个 ROI 模板")
    return rois


def _parse_light_alignment(raw: dict[str, Any]) -> dict[str, tuple[float, ...]]:
    alignment: dict[str, tuple[float, ...]] = {}
    for light_id, item in raw.items():
        matrix_raw = _dict(item, f"light_alignment.{light_id}").get("matrix_3x3", [1, 0, 0, 0, 1, 0, 0, 0, 1])
        if not isinstance(matrix_raw, list) or len(matrix_raw) != 9:
            raise RecipeValidationError(f"light_alignment.{light_id}.matrix_3x3 必须包含 9 个数字")
        matrix = tuple(_finite_float(value, f"light_alignment.{light_id}.matrix_3x3") for value in matrix_raw)
        alignment[str(light_id)] = matrix
    return alignment


def _parse_polygon(value: Any, name: str) -> tuple[tuple[int, int], ...]:
    if not isinstance(value, list):
        raise RecipeValidationError(f"{name} 必须是点列表")
    points: list[tuple[int, int]] = []
    for index, point in enumerate(value):
        if not isinstance(point, list) or len(point) != 2:
            raise RecipeValidationError(f"{name}[{index}] 必须是 [x, y]")
        points.append((_int(point[0], f"{name}[{index}].x"), _int(point[1], f"{name}[{index}].y")))
    return tuple(points)


def _validate_roi_polygon(roi_name: str, polygon: tuple[tuple[int, int], ...]) -> None:
    if len(set(polygon)) != len(polygon):
        raise RecipeValidationError(f"ROI 存在重复点: {roi_name}")
    if _polygon_area(polygon) <= 0.0:
        raise RecipeValidationError(f"ROI 面积无效: {roi_name}")


def _polygon_area(polygon: tuple[tuple[int, int], ...]) -> float:
    area = 0.0
    for (x0, y0), (x1, y1) in zip(polygon, polygon[1:] + polygon[:1]):
        area += float(x0 * y1 - x1 * y0)
    return abs(area) * 0.5


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecipeValidationError(f"{name} 必须是字典")
    return value


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecipeValidationError(f"{name} 必须是非空字符串")
    return value


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int):
        raise RecipeValidationError(f"{name} 必须是整数")
    return value


def _positive_int(value: Any, name: str) -> int:
    result = _int(value, name)
    if result <= 0:
        raise RecipeValidationError(f"{name} 必须大于 0")
    return result


def _finite_float(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise RecipeValidationError(f"{name} 必须是数字")
    result = float(value)
    if not math.isfinite(result):
        raise RecipeValidationError(f"{name} 必须是有限数字")
    return result
