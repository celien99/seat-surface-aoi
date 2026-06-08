from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob


@dataclass
class PreparedBundle:
    camera_id: str
    rois: dict[str, dict[str, LightFrame]]


class Preprocessor:
    def run(self, job: SeatInspectionJob, recipe: Recipe) -> list[PreparedBundle]:
        prepared: list[PreparedBundle] = []
        for bundle in job.camera_bundles:
            decoded = self._decode_frames(bundle, recipe)
            prepared.append(PreparedBundle(camera_id=bundle.camera_id, rois={"full": decoded}))
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

