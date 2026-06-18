from __future__ import annotations

import json
from pathlib import Path

from display_app.infrastructure.image_provider import CameraImageProvider
from display_app.services.display_bridge import DisplayBridge
from display_app.services.image_loader import load_netpbm_bgr
from display_app.viewmodels.main_viewmodel import MainViewModel


def test_load_netpbm_bgr_supports_pgm_and_ppm(tmp_path: Path) -> None:
    pgm = tmp_path / "sample.pgm"
    pgm.write_bytes(b"P5\n2 1\n255\n\x01\x02")
    pgm_image = load_netpbm_bgr(pgm)

    assert pgm_image.shape == (1, 2, 3)
    assert pgm_image[0, 0].tolist() == [1, 1, 1]

    ppm = tmp_path / "sample.ppm"
    ppm.write_bytes(b"P6\n1 1\n255\n\x0a\x14\x1e")
    ppm_image = load_netpbm_bgr(ppm)

    assert ppm_image.shape == (1, 1, 3)
    assert ppm_image[0, 0].tolist() == [30, 20, 10]


def test_display_bridge_reads_latest_and_publishes_images(tmp_path: Path) -> None:
    image_path = tmp_path / "roi.pgm"
    overlay_path = tmp_path / "overlay.ppm"
    image_path.write_bytes(b"P5\n2 1\n255\n\x01\x02")
    overlay_path.write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")
    _write_latest(
        tmp_path,
        {
            "decision": "NG",
            "defects": [
                {
                    "defect_id": "d1",
                    "class_name": "scratch",
                    "severity": "major",
                    "camera_id": "CAM_FRONT",
                    "pose_id": "POSE_A",
                    "roi_name": "seat",
                    "score": 0.93,
                    "decision": "NG",
                }
            ],
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "POSE_A", "path": str(image_path)}],
            "overlays": [{"camera_id": "CAM_FRONT", "pose_id": "POSE_A", "path": str(overlay_path)}],
        },
    )
    provider = CameraImageProvider()
    bridge = DisplayBridge(tmp_path, provider)

    event = bridge.read_latest()

    assert event is not None
    assert event.decision == "NG"
    assert event.defects[0].class_name == "scratch"
    assert bridge.publish_images(event) == ["CAM_FRONT/POSE_A"]


def test_main_view_model_updates_from_display_event(tmp_path: Path) -> None:
    image_path = tmp_path / "roi.pgm"
    overlay_path = tmp_path / "overlay.ppm"
    image_path.write_bytes(b"P5\n2 1\n255\n\x01\x02")
    overlay_path.write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")
    _write_latest(
        tmp_path,
        {
            "decision": "NG",
            "defect_count": 1,
            "defects": [
                {
                    "defect_id": "d1",
                    "class_name": "scratch",
                    "severity": "major",
                    "camera_id": "CAM_FRONT",
                    "pose_id": "CAM_FRONT",
                    "roi_name": "seat",
                    "score": 0.88,
                    "decision": "NG",
                }
            ],
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(image_path)}],
            "overlays": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(overlay_path)}],
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()))

    view_model.pollLatest()

    assert view_model.cameraList == [
        {
            "cameraId": "CAM_FRONT",
            "live": True,
            "status": "ng",
            "defectLabel": "scratch",
            "frameVersion": 1,
        }
    ]
    assert view_model.okCount == 0
    assert view_model.ngCount == 1
    assert view_model.lastTriggerResult == "NG"
    assert view_model.ngOverlayVisible is True
    assert view_model.logs[0]["defect_type"] == "scratch"


def _write_latest(root: Path, overrides: dict) -> None:
    payload = {
        "schema": "seat_surface_aoi.display_event.v1",
        "timestamp_ms": 1781758399933,
        "source": "python_detector",
        "sequence_id": 1,
        "trigger_id": 1000,
        "seat_id": "SIM_SEAT_1000",
        "sku": "seat_a_black_leather",
        "recipe_id": "seat_a_black_leather_v1",
        "decision": "OK",
        "quality_pass": True,
        "error_code": 0,
        "elapsed_ms": 45.7,
        "defect_count": 0,
        "defects": [],
        "quality_messages": [],
        "message": "",
        "error": {},
        "trace_dir": "",
        "images": [],
        "overlays": [],
    }
    payload.update(overrides)
    (root / "display_latest.json").write_text(json.dumps(payload), encoding="utf-8")
