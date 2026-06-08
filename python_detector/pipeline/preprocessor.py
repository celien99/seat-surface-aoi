from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.calibration_manager import Calibration, CalibrationManager, RoiTemplate
from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob


@dataclass
class PreparedBundle:
    camera_id: str
    calibration: Calibration
    rois: dict[str, dict[str, LightFrame]]
    roi_templates: dict[str, RoiTemplate]


class Preprocessor:
    def __init__(self, calibration_manager: CalibrationManager | None = None) -> None:
        self.calibration_manager = calibration_manager or CalibrationManager()

    def run(self, job: SeatInspectionJob, recipe: Recipe) -> list[PreparedBundle]:
        prepared: list[PreparedBundle] = []
        for bundle in job.camera_bundles:
            camera_recipe = recipe.camera(bundle.camera_id)
            if camera_recipe is None:
                raise ValueError(f"{bundle.camera_id}: 配方未启用该机位")
            calibration = self.calibration_manager.load(
                bundle.camera_id,
                camera_recipe.calibration_id,
                camera_recipe.roi_template,
            )
            decoded = self._decode_frames(bundle, recipe)
            self._assert_calibration_matches(decoded, calibration)
            prepared.append(
                PreparedBundle(
                    camera_id=bundle.camera_id,
                    calibration=calibration,
                    rois={roi_name: decoded for roi_name in calibration.roi_templates},
                    roi_templates=calibration.roi_templates,
                )
            )
        return prepared

    def _decode_frames(self, bundle: CameraBundle, recipe: Recipe) -> dict[str, LightFrame]:
        decoded: dict[str, LightFrame] = {}
        for light_id, frame in bundle.light_frames.items():
            self._assert_supported(frame)
            decoded[light_id] = frame
        return decoded

    def _assert_supported(self, frame: LightFrame) -> None:
        if frame.pixel_format != "MONO8":
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: unsupported pixel_format {frame.pixel_format}")
        if frame.dtype != "UINT8":
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: unsupported dtype {frame.dtype}")
        if frame.channels != 1:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: expected mono image")
        if frame.stride_bytes < frame.width * frame.channels:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: stride smaller than row size")

    def _assert_calibration_matches(self, frames: dict[str, LightFrame], calibration: Calibration) -> None:
        for frame in frames.values():
            if frame.calibration_id != calibration.calibration_id:
                raise ValueError(
                    f"{frame.camera_id}/{frame.light_id}: calibration_id 不一致 "
                    f"{frame.calibration_id} != {calibration.calibration_id}"
                )
            expected_width, expected_height = calibration.image_size
            if frame.width != expected_width or frame.height != expected_height:
                raise ValueError(
                    f"{frame.camera_id}/{frame.light_id}: 图像尺寸与标定不一致 "
                    f"{frame.width}x{frame.height} != {expected_width}x{expected_height}"
                )
