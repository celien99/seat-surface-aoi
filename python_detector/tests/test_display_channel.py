from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from python_detector.algorithm import AlgorithmRun
from python_detector.display_channel import DISPLAY_EVENT_SCHEMA, DisplayChannelWriter, build_display_event
from python_detector.image_codec import write_gray_png, write_rgb_png
from python_detector.ipc.data_types import DefectResult, InspectionResult
from training_tools.job_fixture import make_simulated_job


def test_build_display_event_includes_result_and_trace_assets(tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace" / "20260618" / "SIM_1_1"
    raw_dir = trace_dir / "raw_images"
    overlay_dir = trace_dir / "overlays"
    raw_dir.mkdir(parents=True)
    overlay_dir.mkdir(parents=True)
    write_gray_png(raw_dir / "TOP_BACK_DIFFUSE.png", 1, 1, b"\x08")
    write_rgb_png(overlay_dir / "TOP_BACK_seat.png", 1, 1, b"\xff\x00\x00")
    job = make_simulated_job()
    defect = DefectResult(
        defect_id="D1",
        severity="critical",
        camera_id="TOP_BACK",
        pose_id="TOP_BACK",
        roi_name="seat",
        bbox_xyxy_pixel=(0, 0, 1, 1),
        score=0.91,
        area_px=12,
        evidence_lights=["DIFFUSE"],
        mask_offset=None,
        decision="NG",
    )
    result = InspectionResult(
        sequence_id=job.sequence_id,
        trigger_id=job.trigger_id,
        seat_id=job.seat_id,
        decision="NG",
        defects=[defect],
        quality_pass=True,
        error_code=0,
        elapsed_ms=12.5,
    )
    run = AlgorithmRun(result=result, context={}, trace_dir=trace_dir)

    event = build_display_event(job, run)

    assert event["schema"] == DISPLAY_EVENT_SCHEMA
    assert event["decision"] == "NG"
    assert "class_name" not in event["defects"][0]
    assert event["images"][0]["kind"] == "raw_image"
    assert event["overlays"][0]["path"].endswith(".png")
    assert event["overlays"][0]["camera_id"] == "TOP_BACK"
    assert event["overlays"][0]["pose_id"] == "TOP_BACK"
    assert event["overlays"][0]["roi_name"] == "seat"
    assert "heatmaps" not in event


def test_build_display_event_includes_ok_overlay_without_defects(tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace" / "20260618" / "SIM_OK_1"
    overlay_dir = trace_dir / "overlays"
    overlay_dir.mkdir(parents=True)
    write_rgb_png(overlay_dir / "TOP_BACK_seat.png", 1, 1, b"\x00\xb4\x5a")
    job = make_simulated_job()
    result = InspectionResult(
        sequence_id=job.sequence_id,
        trigger_id=job.trigger_id,
        seat_id=job.seat_id,
        decision="OK",
        defects=[],
        quality_pass=True,
    )

    event = build_display_event(job, AlgorithmRun(result=result, context={}, trace_dir=trace_dir))

    assert event["decision"] == "OK"
    assert event["overlays"] == [
        {
            "kind": "overlay",
            "defect_id": "",
            "camera_id": "TOP_BACK",
            "pose_id": "TOP_BACK",
            "roi_name": "seat",
            "path": str((overlay_dir / "TOP_BACK_seat.png").resolve()),
        }
    ]


def test_display_channel_writer_updates_latest_atomically(tmp_path: Path) -> None:
    job = make_simulated_job()
    result = InspectionResult(
        sequence_id=job.sequence_id,
        trigger_id=job.trigger_id,
        seat_id=job.seat_id,
        decision="OK",
        quality_pass=True,
    )
    writer = DisplayChannelWriter(tmp_path)

    writer.write(job, AlgorithmRun(result=result, context={}, trace_dir=None))
    writer.write(
        replace(job, sequence_id=2, trigger_id=1002),
        AlgorithmRun(result=replace(result, sequence_id=2, trigger_id=1002), context={}, trace_dir=None),
    )

    latest = json.loads((tmp_path / "display_latest.json").read_text(encoding="utf-8"))
    events = (tmp_path / "display_events.jsonl").read_text(encoding="utf-8").splitlines()
    assert latest["sequence_id"] == 2
    assert len(events) == 2
