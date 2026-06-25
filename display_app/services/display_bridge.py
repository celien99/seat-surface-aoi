from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from display_app.infrastructure.image_provider import CameraImageProvider
from display_app.services.image_loader import RasterImageError, load_raster_bgr


DISPLAY_EVENT_SCHEMA = "seat_surface_aoi.display_event.v1"


@dataclass(slots=True)
class DisplayDefect:
    defect_id: str
    class_name: str
    severity: str
    camera_id: str
    pose_id: str
    roi_name: str
    score: float
    decision: str


@dataclass(slots=True)
class DisplayEvent:
    sequence_id: int
    trigger_id: int
    seat_id: str
    sku: str
    recipe_id: str
    decision: str
    quality_pass: bool
    error_code: int
    elapsed_ms: float
    defect_count: int
    defects: list[DisplayDefect] = field(default_factory=list)
    message: str = ""
    trace_dir: str = ""
    images: list[dict[str, Any]] = field(default_factory=list)
    overlays: list[dict[str, Any]] = field(default_factory=list)
    timestamp_ms: int = 0
    raw: dict[str, Any] = field(default_factory=dict)
    source: str = "python_detector"
    asset_unavailable: bool = False
    sample_collection: bool = False

    @property
    def event_key(self) -> tuple[int, int, int]:
        return self.sequence_id, self.trigger_id, self.timestamp_ms


@dataclass(slots=True)
class ControllerEvent:
    timestamp_us: int
    event: str
    sequence_id: int
    trigger_id: int
    seat_id: str
    sku: str
    decision: str
    error: str
    error_code: int
    station_state: str
    alarm_level: str
    message: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def event_key(self) -> tuple[int, int, int]:
        return self.sequence_id, self.trigger_id, self.timestamp_us


class DisplayBridge:
    """Read detector display files and update the QML image provider."""

    def __init__(self, trace_root: str | Path, image_provider: CameraImageProvider) -> None:
        self.trace_root = Path(trace_root)
        self.image_provider = image_provider
        self.latest_path = self.trace_root / "display_latest.json"
        self.controller_events_path = self.trace_root / "cpp_controller_events.jsonl"
        self._controller_offset = 0

    def read_latest(self) -> DisplayEvent | None:
        if not self.latest_path.exists():
            return None
        try:
            payload = json.loads(self.latest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if payload.get("schema") != DISPLAY_EVENT_SCHEMA:
            return None
        return _event_from_payload(payload)

    def publish_images(self, event: DisplayEvent) -> list[str]:
        camera_ids: list[str] = []
        for image in _select_display_images(event.images):
            camera_id = _asset_camera_id(image)
            path = image.get("path")
            if not camera_id or not path:
                continue
            try:
                self.image_provider.update_frame(camera_id, load_raster_bgr(path))
            except (OSError, RasterImageError):
                continue
            camera_ids.append(camera_id)

        for overlay in event.overlays:
            camera_id = _asset_camera_id(overlay)
            path = overlay.get("path")
            if not camera_id or not path:
                continue
            try:
                self.image_provider.update_overlay(camera_id, load_raster_bgr(path))
            except (OSError, RasterImageError):
                continue
            camera_ids.append(camera_id)

        return sorted(set(camera_ids))

    def read_controller_events(self, *, limit: int = 100) -> list[ControllerEvent]:
        if not self.controller_events_path.exists():
            return []
        try:
            with self.controller_events_path.open("r", encoding="utf-8") as handle:
                handle.seek(self._controller_offset)
                lines = handle.readlines()
                self._controller_offset = handle.tell()
        except OSError:
            return []
        events: list[ControllerEvent] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                events.append(_controller_event_from_payload(payload))
        return events


def _event_from_payload(payload: dict[str, Any]) -> DisplayEvent:
    defects = [
        DisplayDefect(
            defect_id=str(item.get("defect_id", "")),
            class_name=str(item.get("class_name", "")),
            severity=str(item.get("severity", "")),
            camera_id=str(item.get("camera_id", "")),
            pose_id=str(item.get("pose_id") or item.get("camera_id") or ""),
            roi_name=str(item.get("roi_name", "")),
            score=float(item.get("score", 0.0) or 0.0),
            decision=str(item.get("decision", "")),
        )
        for item in payload.get("defects", [])
        if isinstance(item, dict)
    ]
    return DisplayEvent(
        sequence_id=int(payload.get("sequence_id", 0) or 0),
        trigger_id=int(payload.get("trigger_id", 0) or 0),
        seat_id=str(payload.get("seat_id", "")),
        sku=str(payload.get("sku", "")),
        recipe_id=str(payload.get("recipe_id", "")),
        decision=str(payload.get("decision", "RECHECK") or "RECHECK"),
        quality_pass=bool(payload.get("quality_pass", False)),
        error_code=int(payload.get("error_code", 0) or 0),
        elapsed_ms=float(payload.get("elapsed_ms", 0.0) or 0.0),
        defect_count=int(payload.get("defect_count", len(defects)) or 0),
        defects=defects,
        message=str(payload.get("message", "")),
        trace_dir=str(payload.get("trace_dir", "")),
        images=[item for item in payload.get("images", []) if isinstance(item, dict)],
        overlays=[item for item in payload.get("overlays", []) if isinstance(item, dict)],
        timestamp_ms=int(payload.get("timestamp_ms", 0) or 0),
        raw=payload,
        source=str(payload.get("source", "python_detector") or "python_detector"),
        asset_unavailable=_asset_unavailable(payload),
        sample_collection=_sample_collection_enabled(payload),
    )


def _controller_event_from_payload(payload: dict[str, Any]) -> ControllerEvent:
    return ControllerEvent(
        timestamp_us=int(payload.get("timestamp_us", 0) or 0),
        event=str(payload.get("event", "")),
        sequence_id=int(payload.get("sequence_id", 0) or 0),
        trigger_id=int(payload.get("trigger_id", 0) or 0),
        seat_id=str(payload.get("seat_id", "")),
        sku=str(payload.get("sku", "")),
        decision=str(payload.get("decision", "RECHECK") or "RECHECK"),
        error=str(payload.get("error", "")),
        error_code=int(payload.get("error_code", 0) or 0),
        station_state=str(payload.get("station_state", "")),
        alarm_level=str(payload.get("alarm_level", "")),
        message=str(payload.get("message", "")),
        raw=payload,
    )


def _asset_unavailable(payload: dict[str, Any]) -> bool:
    error = payload.get("error", {})
    if isinstance(error, dict):
        if bool(error.get("asset_unavailable")):
            return True
        asset = error.get("asset", {})
        if isinstance(asset, dict) and asset:
            return True
    return False


def _sample_collection_enabled(payload: dict[str, Any]) -> bool:
    if _asset_unavailable(payload):
        return True
    sample = payload.get("sample_collection", {})
    if isinstance(sample, dict):
        return bool(sample.get("enabled"))
    return False


def _asset_camera_id(asset: dict[str, Any]) -> str:
    camera_id = str(asset.get("camera_id", ""))
    pose_id = str(asset.get("pose_id", ""))
    if pose_id and pose_id != camera_id:
        return f"{camera_id}/{pose_id}"
    return camera_id


def _select_display_images(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_camera: dict[str, dict[str, Any]] = {}
    for image in images:
        camera_id = _asset_camera_id(image)
        if not camera_id:
            continue
        current = by_camera.get(camera_id)
        if current is None or _image_priority(image) < _image_priority(current):
            by_camera[camera_id] = image
    return list(by_camera.values())


def _image_priority(image: dict[str, Any]) -> tuple[int, int, str]:
    kind_rank = 0 if image.get("kind") == "raw_image" else 1
    light_rank = {
        "DIFFUSE": 0,
        "POLAR_DIFFUSE": 1,
        "HIGH_LEFT": 2,
        "HIGH_RIGHT": 3,
    }.get(str(image.get("light_id", "")), 9)
    return kind_rank, light_rank, str(image.get("path", ""))
