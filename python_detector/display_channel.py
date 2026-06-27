from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

from python_detector.algorithm import AlgorithmRun
from python_detector.ipc.data_types import DefectResult, InspectionResult, SeatInspectionJob


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
        "error": _jsonable(error),
        "sample_collection": _jsonable(run.context.get("sample_collection", {})),
        "trace_dir": str(trace_dir) if trace_dir is not None else "",
        "images": _image_assets(trace_dir) if trace_dir is not None else [],
        "overlays": _overlay_assets(trace_dir, result) if trace_dir is not None else [],
        "heatmaps": _patchcore_heatmap_assets(trace_dir) if trace_dir is not None else [],
    }


def _defect_event(defect: DefectResult) -> dict[str, Any]:
    return {
        "defect_id": defect.defect_id,
        "class_name": defect.class_name,
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
    assets = _raw_image_assets(trace_dir)
    assets.extend(_roi_image_assets(trace_dir))
    return assets


def _raw_image_assets(trace_dir: Path) -> list[dict[str, Any]]:
    image_root = trace_dir / "raw_images"
    if not image_root.is_dir():
        return []
    assets: list[dict[str, Any]] = []
    for path in _iter_image_files(image_root):
        rel = path.relative_to(image_root)
        parts = rel.parts
        if len(parts) != 3:
            continue
        camera_id, pose_id = parts[0], parts[1]
        light_id = Path(parts[-1]).stem
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


def _roi_image_assets(trace_dir: Path) -> list[dict[str, Any]]:
    image_root = trace_dir / "images"
    if not image_root.is_dir():
        return []
    assets: list[dict[str, Any]] = []
    for path in _iter_image_files(image_root):
        rel = path.relative_to(image_root)
        parts = rel.parts
        if len(parts) >= 4:
            camera_id, pose_id, roi_name = parts[0], parts[1], parts[2]
            light_id = Path(parts[-1]).stem
        elif len(parts) == 3:
            camera_id, roi_name = parts[0], parts[1]
            pose_id = camera_id
            light_id = Path(parts[-1]).stem
        else:
            continue
        assets.append(
            {
                "kind": "roi_image",
                "camera_id": camera_id,
                "pose_id": pose_id,
                "roi_name": roi_name,
                "light_id": light_id,
                "path": str(path.resolve()),
            }
        )
    return assets


def _overlay_assets(trace_dir: Path, result: InspectionResult) -> list[dict[str, Any]]:
    overlay_root = trace_dir / "overlays"
    if not overlay_root.is_dir():
        return []
    assets: list[dict[str, Any]] = []
    defects_by_name = {
        "_".join(
            [
                _safe_name(defect.defect_id),
                _safe_name(defect.camera_id),
                _safe_name(defect.pose_id or defect.camera_id),
                _safe_name(defect.roi_name),
            ]
        ): defect
        for defect in result.defects
    }
    for path in _iter_image_files(overlay_root):
        rel = path.relative_to(overlay_root)
        parts = rel.parts
        if len(parts) == 3:
            camera_id, pose_id = parts[0], parts[1]
            roi_name = Path(parts[-1]).stem
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
            continue
        defect = defects_by_name.get(path.stem)
        if defect is None and len(result.defects) == 1:
            defect = result.defects[0]
        if defect is None:
            continue
        assets.append(
            {
                "kind": "overlay",
                "defect_id": defect.defect_id,
                "camera_id": defect.camera_id,
                "pose_id": defect.pose_id or defect.camera_id,
                "roi_name": defect.roi_name,
                "path": str(path.resolve()),
            }
        )
    return assets


def _patchcore_heatmap_assets(trace_dir: Path) -> list[dict[str, Any]]:
    heatmap_root = trace_dir / "patchcore_heatmaps"
    if not heatmap_root.is_dir():
        return []
    assets: list[dict[str, Any]] = []
    for path in _iter_image_files(heatmap_root):
        rel = path.relative_to(heatmap_root)
        parts = rel.parts
        if len(parts) != 3:
            continue
        camera_id, pose_id = parts[0], parts[1]
        roi_name = Path(parts[-1]).stem
        assets.append(
            {
                "kind": "patchcore_heatmap",
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


def _message(quality_messages: list[str], error: Any) -> str:
    if quality_messages:
        return "；".join(quality_messages)
    if isinstance(error, dict):
        return str(error.get("message") or error.get("type") or "")
    return ""


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, memoryview):
        return {"memoryview_bytes": len(value)}
    if hasattr(value, "value"):
        return value.value
    return value
