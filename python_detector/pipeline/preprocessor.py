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
            rois = {
                roi_name: {
                    light_id: self._crop_to_roi(frame, roi)
                    for light_id, frame in decoded.items()
                }
                for roi_name, roi in calibration.roi_templates.items()
            }
            prepared.append(
                PreparedBundle(
                    camera_id=bundle.camera_id,
                    calibration=calibration,
                    rois=rois,
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
        if len(frame.image) < frame.stride_bytes * frame.height:
            raise ValueError(f"{frame.camera_id}/{frame.light_id}: image shorter than stride")

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

    def _crop_to_roi(self, frame: LightFrame, roi: RoiTemplate) -> LightFrame:
        x_values = [point[0] for point in roi.polygon_xy]
        y_values = [point[1] for point in roi.polygon_xy]
        x0 = min(x_values)
        y0 = min(y_values)
        x1 = max(x_values)
        y1 = max(y_values)
        if x0 < 0 or y0 < 0 or x1 >= frame.width or y1 >= frame.height:
            raise ValueError(
                f"{frame.camera_id}/{frame.light_id}/{roi.roi_name}: ROI 坐标越界 "
                f"({x0},{y0})-({x1},{y1}) image={frame.width}x{frame.height}"
            )
        crop_width = x1 - x0 + 1
        crop_height = y1 - y0 + 1
        if roi.output_size != (crop_width, crop_height):
            raise ValueError(
                f"{frame.camera_id}/{frame.light_id}/{roi.roi_name}: ROI output_size 与裁剪尺寸不一致 "
                f"{roi.output_size} != {(crop_width, crop_height)}"
            )

        cropped = bytearray(crop_width * crop_height)
        for row in range(crop_height):
            source_start = (y0 + row) * frame.stride_bytes + x0
            source_end = source_start + crop_width
            target_start = row * crop_width
            cropped[target_start : target_start + crop_width] = frame.image[source_start:source_end]
        origin_x, origin_y = frame.origin_xy
        return LightFrame(
            camera_id=frame.camera_id,
            light_id=frame.light_id,
            frame_index=frame.frame_index,
            light_seq_index=frame.light_seq_index,
            width=crop_width,
            height=crop_height,
            channels=frame.channels,
            stride_bytes=crop_width,
            pixel_format=frame.pixel_format,
            bit_depth=frame.bit_depth,
            color_order=frame.color_order,
            dtype=frame.dtype,
            timestamp_us=frame.timestamp_us,
            exposure_us=frame.exposure_us,
            gain=frame.gain,
            calibration_id=frame.calibration_id,
            image_crc32=frame.image_crc32,
            image=memoryview(cropped),
            origin_xy=(origin_x + x0, origin_y + y0),
            source_width=frame.source_width or frame.width,
            source_height=frame.source_height or frame.height,
        )
