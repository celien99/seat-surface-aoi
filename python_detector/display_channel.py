from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from python_detector.algorithm import AlgorithmRun
from python_detector.ipc.data_types import DefectResult, InspectionResult, SeatInspectionJob, jsonable_result


DISPLAY_EVENT_SCHEMA = "seat_surface_aoi.display_event.v1"


class DisplayChannelWriter:
    """Write detector results for a read-only PySide6/QML frontend."""

    def __init__(self, root_dir: str | Path = "trace") -> None:
        self.root_dir = Path(root_dir)

    @property
    def latest_path(self) -> Path:
        return self.root_dir / "display_latest.json"

    @property
    def events_path(self) -> Path:
        return self.root_dir / "display_events.jsonl"

    def write(self, job: SeatInspectionJob, run: AlgorithmRun) -> dict[str, Any]:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        event = build_display_event(job, run)
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
        with self.events_path.open("a", encoding="utf-8") as output:
            output.write(line)
            output.write("\n")
        self._write_latest(event)
        return event

    def _write_latest(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.latest_path.name}.",
            suffix=".tmp",
            dir=str(self.root_dir),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
                output.write("\n")
            os.replace(tmp_name, self.latest_path)
        finally:
            tmp_path = Path(tmp_name)
            if tmp_path.exists():
                tmp_path.unlink()


def build_display_event(job: SeatInspectionJob, run: AlgorithmRun) -> dict[str, Any]:
    result = run.result
    trace_dir = run.trace_dir.resolve() if run.trace_dir is not None else None
    quality_messages = _quality_messages(run.context.get("quality_report"))
    error = run.context.get("error", {})
    return {
        "schema": DISPLAY_EVENT_SCHEMA,
        "timestamp_ms": int(time.time() * 1000),
        "source": "python_detector",
        "sequence_id": result.sequence_id,
        "trigger_id": result.trigger_id,
        "seat_id": result.seat_id,
        "sku": job.sku,
        "recipe_id": job.recipe_id,
        "decision": result.decision,
        "quality_pass": result.quality_pass,
        "error_code": result.error_code,
        "elapsed_ms": result.elapsed_ms,
        "defect_count": len(result.defects),
        "defects": [_defect_event(defect) for defect in result.defects],
        "quality_messages": quality_messages,
        "message": _message(quality_messages, error),
        "error": jsonable_result(error),
        "sample_collection": jsonable_result(run.context.get("sample_collection", {})),
        "trace_dir": str(trace_dir) if trace_dir is not None else "",
        "images": _image_assets(trace_dir) if trace_dir is not None else [],
        "overlays": _overlay_assets(trace_dir, result) if trace_dir is not None else [],
    }


def _defect_event(defect: DefectResult) -> dict[str, Any]:
    return {
        "defect_id": defect.defect_id,
        "severity": defect.severity,
        "camera_id": defect.camera_id,
        "pose_id": defect.pose_id or defect.camera_id,
        "roi_name": defect.roi_name,
        "bbox_xyxy_pixel": list(defect.bbox_xyxy_pixel),
        "score": defect.score,
        "area_px": defect.area_px,
        "evidence_lights": list(defect.evidence_lights),
        "decision": defect.decision,
    }


def _image_assets(trace_dir: Path) -> list[dict[str, Any]]:
    return _raw_image_assets(trace_dir)


def _raw_image_assets(trace_dir: Path) -> list[dict[str, Any]]:
    """解析扁平化的 raw_images：{camera}[_{pose}]_{light}.png。"""
    image_root = trace_dir / "raw_images"
    if not image_root.is_dir():
        return []
    assets: list[dict[str, Any]] = []
    _KNOWN_LIGHTS = {"DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"}
    for path in _iter_image_files(image_root):
        stem = path.stem
        light_id = ""
        for known in sorted(_KNOWN_LIGHTS, key=len, reverse=True):
            if stem.endswith(known):
                light_id = known
                break
        if not light_id:
            continue
        prefix = stem[: -len(light_id)].rstrip("_")
        # prefix 格式: camera_id 或 camera_id_pose_id
        # camera_id 如 TOP_BACK 含下划线，需要区分到底哪段是 camera
        # 策略: 从已知 camera 集合匹配最长前缀
        known_cameras = ["TOP_BACK", "TOP_CUSHION", "EYE_IN_HAND"]
        camera_id = ""
        for cam in sorted(known_cameras, key=len, reverse=True):
            if prefix == cam or prefix.startswith(cam + "_"):
                camera_id = cam
                break
        if not camera_id:
            camera_id = prefix
        pose_suffix = prefix[len(camera_id):].lstrip("_")
        pose_id = pose_suffix if pose_suffix else camera_id
        assets.append(
            {
                "kind": "raw_image",
                "camera_id": camera_id,
                "pose_id": pose_id,
                "roi_name": "",
                "light_id": light_id,
                "path": str(path.resolve()),
            }
        )
    return assets


def _overlay_assets(trace_dir: Path, result: InspectionResult) -> list[dict[str, Any]]:
    """解析扁平化的 overlays：{camera}[_{pose}]_{roi}.png。"""
    overlay_root = trace_dir / "overlays"
    if not overlay_root.is_dir():
        return []
    assets: list[dict[str, Any]] = []
    # 从缺陷中收集已知 camera_id，按长度降序用于前缀匹配（处理含下划线的 camera_id）
    known_cameras = sorted({d.camera_id for d in result.defects}, key=len, reverse=True)
    if not known_cameras:
        known_cameras = ["TOP_BACK", "TOP_CUSHION"]
    for path in _iter_image_files(overlay_root):
        stem = path.stem
        camera_id = ""
        for cam in known_cameras:
            if stem.startswith(cam) and (len(stem) == len(cam) or stem[len(cam)] == "_"):
                camera_id = cam
                break
        if not camera_id:
            camera_id = known_cameras[0]
        suffix = stem[len(camera_id):].lstrip("_")
        if "_" in suffix:
            pose_id, roi_name = suffix.rsplit("_", 1)
        else:
            pose_id = camera_id
            roi_name = suffix
        assets.append(
            {
                "kind": "overlay",
                "defect_id": "",
                "camera_id": camera_id,
                "pose_id": pose_id,
                "roi_name": roi_name,
                "path": str(path.resolve()),
            }
        )
    return assets


def _quality_messages(quality_report: Any) -> list[str]:
    messages = getattr(quality_report, "messages", None)
    if not messages:
        return []
    return [str(message) for message in messages if message]


def _iter_image_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".png")


def _message(quality_messages: list[str], error: dict) -> str:
    if quality_messages:
        return "；".join(quality_messages)
    return str(error.get("message") or error.get("type") or "")


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
