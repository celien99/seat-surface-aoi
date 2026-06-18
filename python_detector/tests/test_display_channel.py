from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from python_detector.algorithm import AlgorithmRun
from python_detector.display_channel import DISPLAY_EVENT_SCHEMA, DisplayChannelWriter, build_display_event
from python_detector.ipc.data_types import DefectResult, InspectionResult
from training_tools.job_fixture import make_simulated_job


def test_build_display_event_includes_result_and_trace_assets(tmp_path: Path) -> None:
    trace_dir = tmp_path / "trace" / "20260618" / "SIM_1_1"
    image_dir = trace_dir / "images" / "TOP_BACK" / "TOP_BACK" / "full"
    overlay_dir = trace_dir / "overlays"
    image_dir.mkdir(parents=True)
    overlay_dir.mkdir()
    (image_dir / "DIFFUSE.pgm").write_bytes(b"P5\n1 1\n255\n\x10")
    (overlay_dir / "D1_TOP_BACK_TOP_BACK_full.ppm").write_bytes(b"P6\n1 1\n255\n\xff\x00\x00")
    job = make_simulated_job()
    defect = DefectResult(
        defect_id="D1",
        class_name="scratch",
        severity="critical",
        camera_id="TOP_BACK",
        pose_id="TOP_BACK",
        roi_name="full",
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
    assert event["defects"][0]["class_name"] == "scratch"
    assert event["images"][0]["path"].endswith("DIFFUSE.pgm")
    assert event["overlays"][0]["path"].endswith(".ppm")


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
