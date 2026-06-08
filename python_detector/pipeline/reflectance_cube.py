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
    roi_bbox_xyxy_pixel: tuple[int, int, int, int]

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
        base_light_id = camera_recipe.base_light_id if camera_recipe is not None else recipe.registration.base_light_id
        if base_light_id not in frames:
            base_light_id = recipe.registration.base_light_fallback
        base = frames.get(base_light_id)
        registration = self._registration_report(
            camera_id=camera_id,
            roi_name=roi_name,
            base_light_id=base_light_id,
            frames=frames,
            light_order=light_order,
            bundle=bundle,
            recipe=recipe,
        )
        roi_bbox = base.bbox_xyxy_pixel if base else (0, 0, 0, 0)
        if base is None:
            roi_bbox = next(iter(frames.values())).bbox_xyxy_pixel if frames else (0, 0, 0, 0)
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
            roi_bbox_xyxy_pixel=roi_bbox,
        )

    def _registration_report(
        self,
        camera_id: str,
        roi_name: str,
        base_light_id: str,
        frames: dict[str, LightFrame],
        light_order: tuple[str, ...],
        bundle: PreparedBundle,
        recipe: Recipe,
    ) -> RegistrationReport:
        base = frames.get(base_light_id)
        if base is None:
            return RegistrationReport(
                camera_id=camera_id,
                roi_name=roi_name,
                base_light_id=base_light_id,
                calibration_id="",
                max_error_px=999.0,
                mean_error_px=999.0,
                method="fixed_calibration",
                is_pass=False,
                message="missing base light",
            )

        errors: list[float] = []
        for light_id in light_order:
            if light_id not in frames:
                continue
            matrix = bundle.calibration.light_alignment.get(light_id)
            if matrix is None:
                return RegistrationReport(
                    camera_id=camera_id,
                    roi_name=roi_name,
                    base_light_id=base_light_id,
                    calibration_id=base.calibration_id,
                    max_error_px=999.0,
                    mean_error_px=999.0,
                    method="fixed_calibration",
                    is_pass=False,
                    message=f"missing alignment matrix for {light_id}",
                )
            if len(matrix) != 9:
                return RegistrationReport(
                    camera_id=camera_id,
                    roi_name=roi_name,
                    base_light_id=base_light_id,
                    calibration_id=base.calibration_id,
                    max_error_px=999.0,
                    mean_error_px=999.0,
                    method="fixed_calibration",
                    is_pass=False,
                    message=f"invalid alignment matrix for {light_id}",
                )
            errors.extend(self._corner_errors(frames[light_id], matrix))

        max_error = max(errors, default=0.0)
        mean_error = sum(errors) / max(len(errors), 1)
        is_pass = max_error <= recipe.quality.max_registration_error_px
        return RegistrationReport(
            camera_id=camera_id,
            roi_name=roi_name,
            base_light_id=base_light_id,
            calibration_id=base.calibration_id,
            max_error_px=max_error,
            mean_error_px=mean_error,
            method="fixed_calibration",
            is_pass=is_pass,
            message="fixed calibration alignment pass" if is_pass else "registration error exceeds threshold",
        )

    def _corner_errors(self, frame: LightFrame, matrix: tuple[float, ...]) -> list[float]:
        x0, y0, x1, y1 = frame.bbox_xyxy_pixel
        corners = ((x0, y0), (x1, y0), (x1, y1), (x0, y1))
        errors: list[float] = []
        for x, y in corners:
            mapped = self._apply_homography(matrix, x, y)
            if mapped is None:
                errors.append(999.0)
                continue
            mx, my = mapped
            dx = mx - x
            dy = my - y
            errors.append((dx * dx + dy * dy) ** 0.5)
        return errors

    def _apply_homography(self, matrix: tuple[float, ...], x: int, y: int) -> tuple[float, float] | None:
        denom = matrix[6] * x + matrix[7] * y + matrix[8]
        if abs(denom) < 1e-6:
            return None
        mapped_x = (matrix[0] * x + matrix[1] * y + matrix[2]) / denom
        mapped_y = (matrix[3] * x + matrix[4] * y + matrix[5]) / denom
        return mapped_x, mapped_y
