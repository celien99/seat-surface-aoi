from __future__ import annotations

import json
from pathlib import Path

from display_app.infrastructure.image_provider import CameraImageProvider
from display_app.services.display_bridge import DisplayBridge
from display_app.services.image_loader import load_netpbm_bgr
from display_app.services.operator_journal import OperatorJournal
from display_app.viewmodels.main_viewmodel import MainViewModel
from python_detector.image_codec import write_gray_png, write_rgb_png


def test_load_netpbm_bgr_supports_png_pgm_and_ppm(tmp_path: Path) -> None:
    png = tmp_path / "sample.png"
    write_gray_png(png, 2, 1, b"\x01\x02")
    png_image = load_netpbm_bgr(png)
    assert png_image.shape == (1, 2, 3)
    assert png_image[0, 0].tolist() == [1, 1, 1]

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
    raw_path = tmp_path / "raw.png"
    image_path = tmp_path / "roi.png"
    overlay_path = tmp_path / "overlay.png"
    write_gray_png(raw_path, 2, 1, b"\x09\x09")
    write_gray_png(image_path, 2, 1, b"\x01\x02")
    write_rgb_png(overlay_path, 1, 1, b"\xff\x00\x00")
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
            "images": [
                {
                    "kind": "raw_image",
                    "camera_id": "CAM_FRONT",
                    "pose_id": "POSE_A",
                    "light_id": "DIFFUSE",
                    "path": str(raw_path),
                },
                {
                    "kind": "roi_image",
                    "camera_id": "CAM_FRONT",
                    "pose_id": "POSE_A",
                    "roi_name": "seat",
                    "light_id": "DIFFUSE",
                    "path": str(image_path),
                },
            ],
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
    assert provider.requestImage("CAM_FRONT/POSE_A_original", None, None).pixelColor(0, 0).red() == 9
    assert provider.requestImage("CAM_FRONT/POSE_A_overlay", None, None).pixelColor(0, 0).red() == 255


def test_camera_image_provider_reports_missing_image_as_error() -> None:
    provider = CameraImageProvider()

    image = provider.requestImage("CAM_FRONT/POSE_A_original", None, None)

    assert image.isNull()


def test_display_bridge_publishes_raw_image_when_roi_is_unavailable(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.png"
    write_gray_png(raw_path, 2, 1, b"\x09\x0a")
    _write_latest(
        tmp_path,
        {
            "decision": "RECHECK",
            "quality_pass": True,
            "error_code": 13,
            "message": "模型资产未就绪，保存采集样本",
            "error": {"asset_unavailable": True},
            "sample_collection": {"enabled": True, "reason": "model_asset_unavailable"},
            "images": [
                {
                    "kind": "raw_image",
                    "camera_id": "CAM_FRONT",
                    "pose_id": "POSE_A",
                    "light_id": "DIFFUSE",
                    "path": str(raw_path),
                }
            ],
        },
    )
    provider = CameraImageProvider()
    bridge = DisplayBridge(tmp_path, provider)

    event = bridge.read_latest()

    assert event is not None
    assert event.decision == "RECHECK"
    assert event.asset_unavailable is True
    assert event.sample_collection is True
    assert bridge.publish_images(event) == ["CAM_FRONT/POSE_A"]
    assert provider.requestImage("CAM_FRONT/POSE_A_original", None, None).pixelColor(0, 0).red() == 9


def test_display_bridge_reads_detection_events_with_offset(tmp_path: Path) -> None:
    _append_display_event(tmp_path, {"sequence_id": 1, "decision": "OK"})
    _append_display_event(tmp_path, {"sequence_id": 2, "decision": "RECHECK", "message": "需要复检"})
    bridge = DisplayBridge(tmp_path, CameraImageProvider())

    events = bridge.read_detection_events()

    assert [event.sequence_id for event in events] == [1, 2]
    assert [event.decision for event in events] == ["OK", "RECHECK"]
    assert bridge.read_detection_events() == []


def test_display_bridge_can_skip_existing_detection_events(tmp_path: Path) -> None:
    _append_display_event(tmp_path, {"sequence_id": 1, "decision": "OK"})
    bridge = DisplayBridge(tmp_path, CameraImageProvider())
    bridge.skip_existing_events()
    _append_display_event(tmp_path, {"sequence_id": 2, "decision": "NG"})

    events = bridge.read_detection_events()

    assert [event.sequence_id for event in events] == [2]


def test_display_bridge_clears_failed_image_publish(tmp_path: Path) -> None:
    ok_path = tmp_path / "ok.png"
    missing_path = tmp_path / "missing.png"
    write_gray_png(ok_path, 2, 1, b"\x09\x0a")
    _write_latest(
        tmp_path,
        {
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(ok_path)}],
        },
    )
    provider = CameraImageProvider()
    bridge = DisplayBridge(tmp_path, provider)
    event = bridge.read_latest()
    assert event is not None
    assert bridge.publish_images(event) == ["CAM_FRONT"]
    assert "CAM_FRONT" in provider._frames

    _write_latest(
        tmp_path,
        {
            "timestamp_ms": _now_ms() + 1,
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(missing_path)}],
        },
    )
    event = bridge.read_latest()
    assert event is not None

    report = bridge.publish_images_report(event)

    assert report.successful_camera_ids == []
    assert report.failed_camera_ids == ["CAM_FRONT"]
    assert "CAM_FRONT" not in provider._frames


def test_main_view_model_updates_from_display_event(tmp_path: Path) -> None:
    image_path = tmp_path / "roi.png"
    overlay_path = tmp_path / "overlay.png"
    write_gray_png(image_path, 2, 1, b"\x01\x02")
    write_rgb_png(overlay_path, 1, 1, b"\xff\x00\x00")
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


def test_main_view_model_marks_all_ng_cameras_from_same_event(tmp_path: Path) -> None:
    back_path = tmp_path / "back.png"
    cushion_path = tmp_path / "cushion.png"
    write_gray_png(back_path, 2, 1, b"\x01\x02")
    write_gray_png(cushion_path, 2, 1, b"\x03\x04")
    _write_latest(
        tmp_path,
        {
            "decision": "NG",
            "defect_count": 2,
            "defects": [
                {
                    "defect_id": "d1",
                    "class_name": "scratch",
                    "severity": "critical",
                    "camera_id": "TOP_BACK",
                    "pose_id": "TOP_BACK",
                    "roi_name": "seat",
                    "score": 0.72,
                    "decision": "NG",
                },
                {
                    "defect_id": "d2",
                    "class_name": "dent",
                    "severity": "critical",
                    "camera_id": "TOP_CUSHION",
                    "pose_id": "TOP_CUSHION",
                    "roi_name": "seat",
                    "score": 0.68,
                    "decision": "NG",
                },
            ],
            "images": [
                {"camera_id": "TOP_BACK", "pose_id": "TOP_BACK", "path": str(back_path)},
                {"camera_id": "TOP_CUSHION", "pose_id": "TOP_CUSHION", "path": str(cushion_path)},
            ],
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()))

    view_model.pollLatest()

    by_camera = {item["cameraId"]: item for item in view_model.cameraList}
    assert by_camera["TOP_BACK"]["status"] == "ng"
    assert by_camera["TOP_BACK"]["defectLabel"] == "scratch"
    assert by_camera["TOP_CUSHION"]["status"] == "ng"
    assert by_camera["TOP_CUSHION"]["defectLabel"] == "dent"
    assert view_model.ngOverlayVisible is True
    assert view_model.ngCameraId == "TOP_BACK"
    assert view_model.ngCameraCount == 2
    assert view_model.ngDefectCount == 2
    assert view_model.ngCameraItems == [
        {
            "cameraId": "TOP_BACK",
            "defectLabel": "scratch",
            "defectCount": 1,
            "confidence": 0.72,
        },
        {
            "cameraId": "TOP_CUSHION",
            "defectLabel": "dent",
            "defectCount": 1,
            "confidence": 0.68,
        },
    ]
    assert "TOP_BACK(1)" in view_model.ngAffectedCameras
    assert "TOP_CUSHION(1)" in view_model.ngAffectedCameras


def test_main_view_model_counts_all_detection_events_between_polls(tmp_path: Path) -> None:
    image_path = tmp_path / "roi.png"
    write_gray_png(image_path, 2, 1, b"\x01\x02")
    _append_display_event(
        tmp_path,
        {
            "sequence_id": 1,
            "timestamp_ms": _now_ms(),
            "decision": "OK",
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(image_path)}],
        },
    )
    _append_display_event(
        tmp_path,
        {
            "sequence_id": 2,
            "timestamp_ms": _now_ms() + 1,
            "decision": "RECHECK",
            "message": "质量门禁失败",
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(image_path)}],
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()

    assert view_model.ok == 1
    assert view_model.recheck == 1
    assert view_model.total == 2
    assert len(view_model.logs) == 2
    assert view_model.lastTriggerResult == "RECHECK"


def test_main_view_model_marks_model_unavailable_as_sampling_mode(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.png"
    write_gray_png(raw_path, 2, 1, b"\x09\x0a")
    _write_latest(
        tmp_path,
        {
            "decision": "RECHECK",
            "quality_pass": True,
            "error_code": 13,
            "message": "模型资产未就绪，保存采集样本",
            "error": {"asset_unavailable": True},
            "sample_collection": {"enabled": True},
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(raw_path)}],
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()

    assert view_model.operationMode == "采样模式"
    assert view_model.recheck == 1
    assert view_model.error == 0
    assert "模型资产未就绪" in view_model.statusMessage
    assert (tmp_path / "display_operator_events.jsonl").exists()


def test_display_bridge_reads_cpp_controller_events(tmp_path: Path) -> None:
    event_path = tmp_path / "cpp_controller_events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "timestamp_us": 1781758399933000,
                "event": "inspection_recheck",
                "sequence_id": 7,
                "trigger_id": 1007,
                "seat_id": "S7",
                "sku": "seat_a_black_leather",
                "decision": "RECHECK",
                "error": "DetectorTimeout",
                "error_code": 5,
                "station_state": "Ready",
                "alarm_level": "Warning",
                "message": "detector result timeout",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    bridge = DisplayBridge(tmp_path, CameraImageProvider())

    events = bridge.read_controller_events()

    assert len(events) == 1
    assert events[0].error == "DetectorTimeout"
    assert events[0].message == "detector result timeout"
    assert bridge.read_controller_events() == []


def test_main_view_model_ignores_non_alert_controller_status_events(tmp_path: Path) -> None:
    event_path = tmp_path / "cpp_controller_events.jsonl"
    event_path.write_text(
        json.dumps(
            {
                "timestamp_us": _now_ms() * 1000,
                "event": "station_ready",
                "sequence_id": 0,
                "trigger_id": 0,
                "seat_id": "",
                "sku": "",
                "decision": "RECHECK",
                "error": "None",
                "error_code": 0,
                "station_state": "Ready",
                "alarm_level": "None",
                "message": "station ready",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()

    assert view_model.logs == []
    assert view_model.recheck == 0
    assert not (tmp_path / "display_operator_events.jsonl").exists()


def test_main_view_model_stale_display_latest_updates_image_without_counting(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.png"
    write_gray_png(raw_path, 2, 1, b"\x09\x0a")
    _write_latest(
        tmp_path,
        {
            "timestamp_ms": 1,
            "decision": "RECHECK",
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(raw_path)}],
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()

    assert view_model.cameraList[0]["cameraId"] == "CAM_FRONT"
    assert view_model.lastTriggerResult == "RECHECK"
    assert view_model.recheck == 0
    assert view_model.logs == []


def test_main_view_model_same_latest_refreshes_images_without_counting(tmp_path: Path) -> None:
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    write_gray_png(first_path, 2, 1, b"\x09\x0a")
    write_gray_png(second_path, 2, 1, b"\x0b\x0c")
    timestamp_ms = _now_ms()
    _write_latest(
        tmp_path,
        {
            "timestamp_ms": timestamp_ms,
            "decision": "OK",
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(first_path)}],
        },
    )
    provider = CameraImageProvider()
    view_model = MainViewModel(DisplayBridge(tmp_path, provider), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()
    _write_latest(
        tmp_path,
        {
            "timestamp_ms": timestamp_ms,
            "decision": "OK",
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(second_path)}],
        },
    )
    view_model.pollLatest()

    assert view_model.total == 1
    assert view_model.ok == 1
    assert view_model.cameraList[0]["frameVersion"] == 2
    assert provider.requestImage("CAM_FRONT_original", None, None).pixelColor(0, 0).red() == 11


def test_main_view_model_same_latest_does_not_redecode_unchanged_images(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    write_gray_png(image_path, 2, 1, b"\x09\x0a")
    timestamp_ms = _now_ms()
    _write_latest(
        tmp_path,
        {
            "timestamp_ms": timestamp_ms,
            "decision": "OK",
            "images": [{"camera_id": "CAM_FRONT", "pose_id": "CAM_FRONT", "path": str(image_path)}],
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()
    view_model.pollLatest()

    assert view_model.total == 1
    assert view_model.ok == 1
    assert view_model.cameraList[0]["frameVersion"] == 1


def test_main_view_model_stale_ng_latest_does_not_show_overlay(tmp_path: Path) -> None:
    image_path = tmp_path / "roi.png"
    write_gray_png(image_path, 2, 1, b"\x01\x02")
    _write_latest(
        tmp_path,
        {
            "timestamp_ms": 1,
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
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()

    assert view_model.lastTriggerResult == "NG"
    assert view_model.ng == 0
    assert view_model.logs == []
    assert view_model.ngOverlayVisible is False


def test_main_view_model_persists_review_actions(tmp_path: Path) -> None:
    image_path = tmp_path / "roi.png"
    write_gray_png(image_path, 2, 1, b"\x01\x02")
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
        },
    )
    view_model = MainViewModel(DisplayBridge(tmp_path, CameraImageProvider()), journal=OperatorJournal(tmp_path))

    view_model.pollLatest()
    view_model.markReview()
    view_model.confirmAsDefect(1)

    assert view_model.reviews == []
    assert (tmp_path / "display_review_queue.json").exists()
    journal_lines = (tmp_path / "display_operator_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert any("mark_review" in line for line in journal_lines)
    assert any("review_confirm_defect" in line for line in journal_lines)


def _write_latest(root: Path, overrides: dict) -> None:
    (root / "display_latest.json").write_text(json.dumps(_display_payload(overrides)), encoding="utf-8")


def _append_display_event(root: Path, overrides: dict) -> None:
    with (root / "display_events.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(_display_payload(overrides), ensure_ascii=False))
        output.write("\n")


def _display_payload(overrides: dict) -> dict:
    payload = {
        "schema": "seat_surface_aoi.display_event.v1",
        "timestamp_ms": _now_ms(),
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
        "sample_collection": {},
        "trace_dir": "",
        "images": [],
        "overlays": [],
    }
    payload.update(overrides)
    return payload


def _now_ms() -> int:
    import time

    return int(time.time() * 1000)
