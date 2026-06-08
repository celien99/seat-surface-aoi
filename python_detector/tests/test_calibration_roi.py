import pytest

from python_detector.config.calibration_manager import CalibrationManager
from python_detector.config.recipe_schema import RecipeManager
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob
from python_detector.pipeline.pipeline import InspectionPipeline


def _frame(light_id: str, calibration_id: str = "calib/simulated_v1") -> LightFrame:
    data = bytearray(
        80 + (((x // 2 + y // 2) % 2) * 20) + ((x + 3 * y) % 12)
        for y in range(48)
        for x in range(64)
    )
    return LightFrame(
        camera_id="TOP_BACK",
        light_id=light_id,
        frame_index=1,
        light_seq_index=1,
        width=64,
        height=48,
        channels=1,
        stride_bytes=64,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=1,
        exposure_us=800,
        gain=1.0,
        calibration_id=calibration_id,
        image_crc32=0,
        image=memoryview(data),
    )


def test_calibration_manager_loads_identity_roi() -> None:
    calibration = CalibrationManager().load(
        "TOP_BACK",
        "calib/simulated_v1",
        "python_detector/config/roi/default_roi.yaml",
    )
    assert calibration.roi_templates["full"].output_size == (64, 48)
    assert calibration.light_alignment["DIFFUSE"] == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def test_calibration_mismatch_returns_error_not_ok() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    frames = {
        light: _frame(light, calibration_id="calib/wrong")
        for light in ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    }
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[CameraBundle(camera_id="TOP_BACK", pose_id="TOP_BACK", light_frames=frames)],
    )
    result = InspectionPipeline().process(job, recipe)
    assert result.decision == "ERROR"
    assert result.quality_pass is False

