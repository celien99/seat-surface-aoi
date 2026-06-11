from __future__ import annotations

from dataclasses import dataclass, field

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import LightFrame, SeatInspectionJob
from python_detector.pipeline.ecc_registration import EccAlignmentResult, EccRegistration
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
    details: list[dict[str, object]] = field(default_factory=list)
    pose_id: str = ""


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
    roi_to_source_matrix: tuple[float, ...] | None = None
    source_to_roi_matrix: tuple[float, ...] | None = None
    pose_id: str = ""

    def get(self, light_id: str) -> LightFrame | None:
        return self.frames.get(light_id)


class ReflectanceCubeBuilder:
    def __init__(self, ecc_registration: EccRegistration | None = None) -> None:
        self.ecc_registration = ecc_registration or EccRegistration()

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
        camera_recipe = recipe.camera(camera_id, bundle.pose_id)
        light_order = camera_recipe.light_order if camera_recipe is not None else recipe.light_order
        base_light_id = camera_recipe.base_light_id if camera_recipe is not None else recipe.registration.base_light_id
        if base_light_id not in frames:
            base_light_id = recipe.registration.base_light_fallback
        base = frames.get(base_light_id)
        registration, registered_frames = self._registration_report(
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
        transform_frame = base or next(iter(frames.values()), None)
        return ReflectanceCube(
            sequence_id=job.sequence_id,
            trigger_id=job.trigger_id,
            seat_id=job.seat_id,
            camera_id=camera_id,
            roi_name=roi_name,
            base_light_id=base_light_id,
            light_order=light_order,
            frames={light_id: registered_frames[light_id] for light_id in light_order if light_id in registered_frames},
            registration=registration,
            pixel_size_mm=bundle.calibration.pixel_size_mm,
            calibration_id=bundle.calibration.calibration_id,
            roi_bbox_xyxy_pixel=roi_bbox,
            roi_to_source_matrix=transform_frame.roi_to_source_matrix if transform_frame is not None else None,
            source_to_roi_matrix=transform_frame.source_to_roi_matrix if transform_frame is not None else None,
            pose_id=bundle.pose_id,
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
    ) -> tuple[RegistrationReport, dict[str, LightFrame]]:
        base = frames.get(base_light_id)
        if recipe.registration.method == "ecc":
            return self._ecc_registration_report(
                camera_id=camera_id,
                pose_id=bundle.pose_id,
                roi_name=roi_name,
                base_light_id=base_light_id,
                frames=frames,
                light_order=light_order,
                recipe=recipe,
            )
        if base is None:
            return (
                RegistrationReport(
                    camera_id=camera_id,
                    roi_name=roi_name,
                    pose_id=bundle.pose_id,
                    base_light_id=base_light_id,
                    calibration_id="",
                    max_error_px=999.0,
                    mean_error_px=999.0,
                    method=recipe.registration.method,
                    is_pass=False,
                    message="missing base light",
                ),
                frames,
            )

        errors: list[float] = []
        for light_id in light_order:
            if light_id not in frames:
                continue
            matrix = bundle.calibration.light_alignment.get(light_id)
            if matrix is None:
                return (
                    RegistrationReport(
                        camera_id=camera_id,
                        roi_name=roi_name,
                        pose_id=bundle.pose_id,
                        base_light_id=base_light_id,
                        calibration_id=base.calibration_id,
                        max_error_px=999.0,
                        mean_error_px=999.0,
                        method=recipe.registration.method,
                        is_pass=False,
                        message=f"missing alignment matrix for {light_id}",
                    ),
                    frames,
                )
            if len(matrix) != 9:
                return (
                    RegistrationReport(
                        camera_id=camera_id,
                        roi_name=roi_name,
                        pose_id=bundle.pose_id,
                        base_light_id=base_light_id,
                        calibration_id=base.calibration_id,
                        max_error_px=999.0,
                        mean_error_px=999.0,
                        method=recipe.registration.method,
                        is_pass=False,
                        message=f"invalid alignment matrix for {light_id}",
                    ),
                    frames,
                )
            errors.extend(self._corner_errors(frames[light_id], matrix))

        max_error = max(errors, default=0.0)
        mean_error = sum(errors) / max(len(errors), 1)
        is_pass = max_error <= recipe.quality.max_registration_error_px
        return (
            RegistrationReport(
                camera_id=camera_id,
                roi_name=roi_name,
                pose_id=bundle.pose_id,
                base_light_id=base_light_id,
                calibration_id=base.calibration_id,
                max_error_px=max_error,
                mean_error_px=mean_error,
                method=recipe.registration.method,
                is_pass=is_pass,
                message="fixed calibration alignment pass" if is_pass else "registration error exceeds threshold",
            ),
            frames,
        )

    def _ecc_registration_report(
        self,
        camera_id: str,
        pose_id: str,
        roi_name: str,
        base_light_id: str,
        frames: dict[str, LightFrame],
        light_order: tuple[str, ...],
        recipe: Recipe,
    ) -> tuple[RegistrationReport, dict[str, LightFrame]]:
        base = frames.get(base_light_id)
        if base is None:
            return (
                RegistrationReport(
                    camera_id=camera_id,
                    roi_name=roi_name,
                    pose_id=pose_id,
                    base_light_id=base_light_id,
                    calibration_id="",
                    max_error_px=999.0,
                    mean_error_px=999.0,
                    method="ecc",
                    is_pass=False,
                    message="missing base light",
                ),
                frames,
            )

        alignments: list[EccAlignmentResult] = []
        for light_id in light_order:
            frame = frames.get(light_id)
            if frame is None or light_id == base_light_id:
                continue
            alignments.append(
                self.ecc_registration.align_translation(
                    base,
                    frame,
                    recipe.registration.search_radius_px,
                    recipe.registration.max_iterations,
                    recipe.registration.convergence_epsilon,
                    recipe.registration.min_correlation,
                )
            )
        failed = [result for result in alignments if not result.converged]
        max_error = max((result.mean_error_px for result in alignments), default=0.0)
        mean_error = sum(result.mean_error_px for result in alignments) / max(len(alignments), 1)
        is_pass = not failed and max_error <= recipe.quality.max_registration_error_px
        report = RegistrationReport(
            camera_id=camera_id,
            roi_name=roi_name,
            pose_id=pose_id,
            base_light_id=base_light_id,
            calibration_id=base.calibration_id,
            max_error_px=max_error,
            mean_error_px=mean_error,
            method="ecc",
            is_pass=is_pass,
            message="ECC alignment pass" if is_pass else "ECC alignment failed or exceeded threshold",
            details=[
                {
                    "light_id": result.light_id,
                    "matrix_3x3": list(result.matrix_3x3),
                    "shift_xy": list(result.shift_xy),
                    "correlation": result.correlation,
                    "iterations": result.iterations,
                    "converged": result.converged,
                    "mean_error_px": result.mean_error_px,
                    "message": result.message,
                    "applied": is_pass and result.converged,
                }
                for result in alignments
            ],
        )
        if not is_pass:
            return report, frames
        return report, self._apply_ecc_alignments(frames, alignments)

    def _apply_ecc_alignments(
        self,
        frames: dict[str, LightFrame],
        alignments: list[EccAlignmentResult],
    ) -> dict[str, LightFrame]:
        aligned = dict(frames)
        for result in alignments:
            frame = frames.get(result.light_id)
            if frame is None or not result.converged:
                continue
            aligned[result.light_id] = self.ecc_registration.apply_translation(frame, result.shift_xy)
        return aligned

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
