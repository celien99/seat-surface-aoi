from python_detector.config.recipe_schema import RecipeManager
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob
from python_detector.pipeline.pipeline import InspectionPipeline


LIGHTS = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")


def _frame(light_id: str, value: int = 80, frame_index: int = 1, timestamp_us: int = 1) -> LightFrame:
    data = bytearray(
        value + (((x // 2 + y // 2) % 2) * 20) + ((x + 3 * y) % 12)
        for y in range(48)
        for x in range(64)
    )
    return LightFrame(
        camera_id="TOP_BACK",
        light_id=light_id,
        frame_index=frame_index,
        light_seq_index=frame_index - 1,
        width=64,
        height=48,
        channels=1,
        stride_bytes=64,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=timestamp_us,
        exposure_us=800,
        gain=1.0,
        calibration_id="calib/simulated_v1",
        image_crc32=0,
        image=memoryview(data),
    )


def _bundle(camera_id: str, lights: tuple[str, ...]) -> CameraBundle:
    frames = {
        light: _frame(light, frame_index=index + 1, timestamp_us=1_000 + index * 100)
        for index, light in enumerate(lights)
    }
    for frame in frames.values():
        frame.camera_id = camera_id
    return CameraBundle(camera_id=camera_id, pose_id=camera_id, light_frames=frames)


def _job(lights: tuple[str, ...], include_cushion: bool = True) -> SeatInspectionJob:
    bundles = [_bundle("TOP_BACK", lights)]
    if include_cushion:
        bundles.append(_bundle("TOP_CUSHION", lights))
    return SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id="seat_a_black_leather_v1",
        sku="seat_a_black_leather",
        camera_bundles=bundles,
    )


def test_pipeline_returns_ok_for_complete_simulated_bundle() -> None:
    pipeline = InspectionPipeline()
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    result = pipeline.process(_job(LIGHTS), recipe)
    assert result.decision == "OK"
    assert result.quality_pass is True


def test_missing_required_light_returns_recheck() -> None:
    pipeline = InspectionPipeline()
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    result = pipeline.process(_job(("DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")), recipe)
    assert result.decision == "RECHECK"
    assert result.quality_pass is False


def test_missing_configured_camera_returns_recheck() -> None:
    pipeline = InspectionPipeline()
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    result = pipeline.process(_job(LIGHTS, include_cushion=False), recipe)
    assert result.decision == "RECHECK"
    assert result.quality_pass is False
    assert "TOP_CUSHION: missing configured camera bundle" in pipeline.last_context["quality_report"].messages


def test_non_monotonic_required_light_timestamp_returns_recheck() -> None:
    pipeline = InspectionPipeline()
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    job = _job(LIGHTS)
    job.camera_bundles[0].light_frames["HIGH_LEFT"].timestamp_us = 500
    result = pipeline.process(job, recipe)
    assert result.decision == "RECHECK"
    assert "TOP_BACK: timestamps are not monotonic by required light order" in pipeline.last_context["quality_report"].messages


def test_duplicate_required_light_frame_index_returns_recheck() -> None:
    pipeline = InspectionPipeline()
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    job = _job(LIGHTS)
    job.camera_bundles[0].light_frames["HIGH_LEFT"].frame_index = job.camera_bundles[0].light_frames["DIFFUSE"].frame_index
    result = pipeline.process(job, recipe)
    assert result.decision == "RECHECK"
    assert "TOP_BACK: duplicate frame_index in required lights" in pipeline.last_context["quality_report"].messages
