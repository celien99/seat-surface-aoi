from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from python_detector.config.recipe_schema import RecipeValidationError


@dataclass(frozen=True)
class RoiTemplate:
    roi_name: str
    polygon_xy: tuple[tuple[int, int], ...]
    output_size: tuple[int, int]


@dataclass(frozen=True)
class Calibration:
    calibration_id: str
    camera_id: str
    image_size: tuple[int, int]
    pixel_size_mm: float | None
    base_light_id: str
    light_alignment: dict[str, tuple[float, ...]]
    roi_templates: dict[str, RoiTemplate]


class CalibrationManager:
    def __init__(self, base_dir: str | Path = ".") -> None:
        self.base_dir = Path(base_dir)
        self._cache: dict[tuple[str, str, str], Calibration] = {}

    def load(self, camera_id: str, calibration_id: str, roi_template_path: str) -> Calibration:
        roi_path = self.base_dir / roi_template_path
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
        if roi_path.exists():
            calibration = self._with_roi_override(calibration, roi_path)
        self._cache[key] = calibration
        return calibration

    def _resolve_calibration_path(self, camera_id: str, calibration_id: str) -> Path:
        filename = calibration_id.split("/")[-1]
        if not filename.endswith(".yaml"):
            filename = f"{filename}.yaml"
        return self.base_dir / "python_detector" / "config" / "calibration" / camera_id / filename

    def _parse_calibration(self, raw: dict[str, Any], camera_id: str, calibration_id: str) -> Calibration:
        actual_camera_id = _str(raw.get("camera_id"), "camera_id")
        actual_calibration_id = _str(raw.get("calibration_id"), "calibration_id")
        if actual_camera_id != camera_id:
            raise RecipeValidationError(f"标定 camera_id 不一致: {actual_camera_id} != {camera_id}")
        if actual_calibration_id != calibration_id:
            raise RecipeValidationError(f"标定 calibration_id 不一致: {actual_calibration_id} != {calibration_id}")
        image_size = _dict(raw.get("image_size"), "image_size")
        light_alignment = {
            str(light_id): tuple(float(v) for v in _dict(item, f"light_alignment.{light_id}").get("matrix_3x3", [1, 0, 0, 0, 1, 0, 0, 0, 1]))
            for light_id, item in _dict(raw.get("light_alignment", {}), "light_alignment").items()
        }
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
        polygon = tuple((int(x), int(y)) for x, y in item.get("polygon_xy", []))
        if len(polygon) < 3:
            raise RecipeValidationError(f"ROI 至少需要 3 个点: {roi_name}")
        output_size_raw = item.get("output_size", [0, 0])
        if not isinstance(output_size_raw, list) or len(output_size_raw) != 2:
            raise RecipeValidationError(f"ROI output_size 必须是两个整数: {roi_name}")
        rois[str(roi_name)] = RoiTemplate(
            roi_name=str(roi_name),
            polygon_xy=polygon,
            output_size=(int(output_size_raw[0]), int(output_size_raw[1])),
        )
    if not rois:
        raise RecipeValidationError("至少需要一个 ROI 模板")
    return rois


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecipeValidationError(f"{name} 必须是字典")
    return value


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecipeValidationError(f"{name} 必须是非空字符串")
    return value
