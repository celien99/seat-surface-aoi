from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import LightFrame, SeatInspectionJob
from python_detector.pipeline.preprocessor import PreparedBundle


@dataclass
class RegistrationReport:
    camera_id: str
    roi_name: str
    base_light_id: str
    calibration_id: str
    max_error_px: float
    mean_error_px: float
    method: str
    is_pass: bool
    message: str


@dataclass
class ReflectanceCube:
    sequence_id: int
    trigger_id: int
    seat_id: str
    camera_id: str
    roi_name: str
    base_light_id: str
    light_order: tuple[str, ...]
    frames: dict[str, LightFrame]
    registration: RegistrationReport
    pixel_size_mm: float | None
    calibration_id: str

    def get(self, light_id: str) -> LightFrame | None:
        return self.frames.get(light_id)


class ReflectanceCubeBuilder:
    def build(self, job: SeatInspectionJob, prepared_bundles: list[PreparedBundle], recipe: Recipe) -> list[ReflectanceCube]:
        cubes: list[ReflectanceCube] = []
        for bundle in prepared_bundles:
            for roi_name, frames in bundle.rois.items():
                cubes.append(self._build_roi_cube(job, bundle, roi_name, frames, recipe))
        return cubes

    def _build_roi_cube(
        self,
        job: SeatInspectionJob,
        bundle: PreparedBundle,
        roi_name: str,
        frames: dict[str, LightFrame],
        recipe: Recipe,
    ) -> ReflectanceCube:
        camera_id = bundle.camera_id
        camera_recipe = recipe.camera(camera_id)
        light_order = camera_recipe.light_order if camera_recipe is not None else recipe.light_order
        base_light_id = recipe.registration.base_light_id
        if base_light_id not in frames:
            base_light_id = recipe.registration.base_light_fallback
        base = frames.get(base_light_id)
        registration = RegistrationReport(
            camera_id=camera_id,
            roi_name=roi_name,
            base_light_id=base_light_id,
            calibration_id=base.calibration_id if base else "",
            max_error_px=0.0 if base else 999.0,
            mean_error_px=0.0 if base else 999.0,
            method="fixed_calibration",
            is_pass=base is not None,
            message="simulated identity alignment" if base else "missing base light",
        )
        return ReflectanceCube(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            camera_id=camera_id,
            roi_name=roi_name,
            base_light_id=base_light_id,
            light_order=light_order,
            frames={light_id: frames[light_id] for light_id in light_order if light_id in frames},
            registration=registration,
            pixel_size_mm=bundle.calibration.pixel_size_mm,
            calibration_id=bundle.calibration.calibration_id,
        )
