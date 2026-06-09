from __future__ import annotations

from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob


def make_simulated_job(sequence_id: int = 1) -> SeatInspectionJob:
    bundles = [
        CameraBundle(camera_id="TOP_BACK", pose_id="TOP_BACK", light_frames=_frames("TOP_BACK")),
        CameraBundle(camera_id="TOP_CUSHION", pose_id="TOP_CUSHION", light_frames=_frames("TOP_CUSHION")),
    ]
    return SeatInspectionJob(
        sequence_id=sequence_id,
        trigger_id=1000 + sequence_id,
        seat_id=f"SIM_{sequence_id}",
        recipe_id="seat_a_black_leather_v1",
        sku="seat_a_black_leather",
        camera_bundles=bundles,
    )


def _frames(camera_id: str) -> dict[str, LightFrame]:
    return {
        light_id: _frame(camera_id, light_id, index + 1)
        for index, light_id in enumerate(("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"))
    }


def _frame(camera_id: str, light_id: str, frame_index: int) -> LightFrame:
    data = bytearray(
        80 + (((x // 4 + y // 4) % 2) * 20) + ((x + 3 * y) % 32)
        for y in range(48)
        for x in range(64)
    )
    return LightFrame(
        camera_id=camera_id,
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
        timestamp_us=1_000 + (frame_index - 1) * 100,
        exposure_us=800,
        gain=1.0,
        calibration_id="calib/simulated_v1",
        image_crc32=0,
        image=memoryview(data),
    )

