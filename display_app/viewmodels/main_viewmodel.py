from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot

from display_app.services.display_bridge import DisplayBridge, DisplayDefect, DisplayEvent


@dataclass(slots=True)
class DisplayStats:
    total: int = 0
    ok: int = 0
    ng: int = 0
    recheck: int = 0
    error: int = 0
    defect_types: dict[str, int] = field(default_factory=dict)


class MainViewModel(QObject):
    """MainScreen-compatible ViewModel backed by detector display JSON."""

    lineIdChanged = Signal()
    systemStatusChanged = Signal()
    okCountChanged = Signal()
    ngCountChanged = Signal()
    tactRateChanged = Signal()
    ngOverlayVisibleChanged = Signal()
    ngDefectTypeChanged = Signal()
    ngConfidenceChanged = Signal()
    ngCameraIdChanged = Signal()
    ngImageVersionChanged = Signal()
    cameraListChanged = Signal()
    remainingSecondsChanged = Signal()
    lineStatusChanged = Signal()
    lineConnectedChanged = Signal()
    lineBusyChanged = Signal()
    lastTriggerResultChanged = Signal()
    triggerErrorChanged = Signal()
    triggerErrorDisplayChanged = Signal()
    triggerEnabledChanged = Signal()
    gridLayoutChanged = Signal()
    logsChanged = Signal()
    statsChanged = Signal()
    distributionChanged = Signal()
    reviewsChanged = Signal()

    def __init__(
        self,
        bridge: DisplayBridge,
        *,
        line_id: str = "AOI-1",
        grid_layout: str = "2x2",
        ng_popup_seconds: int = 30,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._line_id = line_id
        self._system_status = "paused"
        self._ok_count = 0
        self._ng_count = 0
        self._tact_rate = 0.0
        self._ng_visible = False
        self._ng_defect_type = ""
        self._ng_confidence = 0.0
        self._ng_camera_id = ""
        self._ng_image_version = 0
        self._remaining_seconds = 0
        self._line_status = "waiting"
        self._line_connected = False
        self._line_busy = False
        self._last_trigger_result = ""
        self._trigger_error = ""
        self._grid_layout = grid_layout
        self._trigger_enabled = False
        self._camera_list: list[dict[str, Any]] = []
        self._camera_index: dict[str, dict[str, Any]] = {}
        self._last_event_key: tuple[int, int, int] | None = None
        self._event_timestamps: list[float] = []
        self._stats = DisplayStats()
        self._logs: list[dict[str, Any]] = []
        self._reviews: list[dict[str, Any]] = []
        self._status_filter = ""
        self._camera_filter = ""
        self._ng_popup_seconds = max(1, int(ng_popup_seconds))
        self._ng_started_at = 0.0

    def _get_line_id(self) -> str:
        return self._line_id

    def _get_system_status(self) -> str:
        return self._system_status

    def _get_ok_count(self) -> int:
        return self._ok_count

    def _get_ng_count(self) -> int:
        return self._ng_count

    def _get_tact_rate(self) -> float:
        return self._tact_rate

    def _get_ng_visible(self) -> bool:
        return self._ng_visible

    def _get_ng_defect_type(self) -> str:
        return self._ng_defect_type

    def _get_ng_confidence(self) -> float:
        return self._ng_confidence

    def _get_ng_camera_id(self) -> str:
        return self._ng_camera_id

    def _get_ng_image_version(self) -> int:
        return self._ng_image_version

    def _get_camera_list(self) -> list:
        return self._camera_list

    def _get_remaining_seconds(self) -> int:
        return self._remaining_seconds

    def _get_line_status(self) -> str:
        return self._line_status

    def _get_line_connected(self) -> bool:
        return self._line_connected

    def _get_line_busy(self) -> bool:
        return self._line_busy

    def _get_last_trigger_result(self) -> str:
        return self._last_trigger_result

    def _get_trigger_error(self) -> str:
        return self._trigger_error

    def _get_trigger_error_display(self) -> str:
        return self._trigger_error

    def _get_trigger_enabled(self) -> bool:
        return self._trigger_enabled

    def _get_grid_layout(self) -> str:
        return self._grid_layout

    def _get_total(self) -> int:
        return self._stats.total

    def _get_ok(self) -> int:
        return self._stats.ok

    def _get_ng(self) -> int:
        return self._stats.ng

    def _get_ok_rate(self) -> float:
        return round(self._stats.ok / max(self._stats.total, 1) * 100.0, 2)

    def _get_defect_distribution(self) -> dict:
        return dict(self._stats.defect_types)

    def _get_logs(self) -> list:
        if not self._status_filter and not self._camera_filter:
            return self._logs
        filtered = self._logs
        if self._status_filter:
            filtered = [item for item in filtered if item.get("status") == self._status_filter]
        if self._camera_filter:
            filtered = [item for item in filtered if item.get("camera_id") == self._camera_filter]
        return filtered

    def _get_reviews(self) -> list:
        return self._reviews

    lineId = Property(str, _get_line_id, notify=lineIdChanged)
    systemStatus = Property(str, _get_system_status, notify=systemStatusChanged)
    okCount = Property(int, _get_ok_count, notify=okCountChanged)
    ngCount = Property(int, _get_ng_count, notify=ngCountChanged)
    tactRate = Property(float, _get_tact_rate, notify=tactRateChanged)
    ngOverlayVisible = Property(bool, _get_ng_visible, notify=ngOverlayVisibleChanged)
    ngDefectType = Property(str, _get_ng_defect_type, notify=ngDefectTypeChanged)
    ngConfidence = Property(float, _get_ng_confidence, notify=ngConfidenceChanged)
    ngCameraId = Property(str, _get_ng_camera_id, notify=ngCameraIdChanged)
    ngImageVersion = Property(int, _get_ng_image_version, notify=ngImageVersionChanged)
    cameraList = Property(list, _get_camera_list, notify=cameraListChanged)
    remainingSeconds = Property(int, _get_remaining_seconds, notify=remainingSecondsChanged)
    lineStatus = Property(str, _get_line_status, notify=lineStatusChanged)
    lineConnected = Property(bool, _get_line_connected, notify=lineConnectedChanged)
    lineBusy = Property(bool, _get_line_busy, notify=lineBusyChanged)
    lastTriggerResult = Property(str, _get_last_trigger_result, notify=lastTriggerResultChanged)
    triggerError = Property(str, _get_trigger_error, notify=triggerErrorChanged)
    triggerErrorDisplay = Property(str, _get_trigger_error_display, notify=triggerErrorDisplayChanged)
    triggerEnabled = Property(bool, _get_trigger_enabled, notify=triggerEnabledChanged)
    gridLayout = Property(str, _get_grid_layout, notify=gridLayoutChanged)
    total = Property(int, _get_total, notify=statsChanged)
    ok = Property(int, _get_ok, notify=statsChanged)
    ng = Property(int, _get_ng, notify=statsChanged)
    okRate = Property(float, _get_ok_rate, notify=statsChanged)
    defectDistribution = Property(dict, _get_defect_distribution, notify=distributionChanged)
    logs = Property(list, _get_logs, notify=logsChanged)
    reviews = Property(list, _get_reviews, notify=reviewsChanged)

    @Slot()
    def pollLatest(self) -> None:
        event = self._bridge.read_latest()
        if event is None:
            self._set_runtime_state(
                system_status="paused",
                line_status="waiting",
                line_connected=False,
                trigger_error="等待检测结果 display_latest.json",
            )
            return
        if event.event_key == self._last_event_key:
            return
        self._last_event_key = event.event_key
        camera_ids = self._bridge.publish_images(event)
        self._apply_event(event, camera_ids)

    @Slot()
    def refreshTriggerState(self) -> None:
        self.pollLatest()
        self._tick_ng_countdown()

    @Slot()
    def acknowledgeNG(self) -> None:
        self._hide_ng_overlay("confirm_defect")

    @Slot()
    def markReview(self) -> None:
        if self._logs:
            review = dict(self._logs[0])
            review["id"] = len(self._reviews) + 1
            self._reviews.insert(0, review)
            self.reviewsChanged.emit()
        self._hide_ng_overlay("mark_review")

    @Slot()
    def dismissFalseAlarm(self) -> None:
        self._hide_ng_overlay("false_alarm")

    @Slot()
    def manualTrigger(self) -> None:
        self._set_trigger_error("当前展示程序只读检测结果，手动触发由 C++ 主控/PLC 负责")

    @Slot()
    def refresh(self) -> None:
        self.statsChanged.emit()
        self.distributionChanged.emit()
        self.logsChanged.emit()
        self.reviewsChanged.emit()

    @Slot(str)
    def setStatusFilter(self, status: str) -> None:
        self._status_filter = status
        self.logsChanged.emit()

    @Slot(str)
    def setCameraFilter(self, camera_id: str) -> None:
        self._camera_filter = camera_id
        self.logsChanged.emit()

    @Slot(str)
    def exportCSV(self, path: str) -> None:
        return None

    @Slot(int)
    def confirmAsDefect(self, record_id: int) -> None:
        self._remove_review(record_id)

    @Slot(int)
    def dismissAsOK(self, record_id: int) -> None:
        self._remove_review(record_id)

    def _apply_event(self, event: DisplayEvent, image_camera_ids: list[str]) -> None:
        status = _normal_status(event.decision)
        defect = _primary_defect(event.defects)
        defect_camera_id = _display_camera_id(defect) if defect else ""
        camera_ids = sorted(set(image_camera_ids + [_display_camera_id(item) for item in event.defects if item.camera_id]))
        if not camera_ids and event.defects:
            camera_ids = sorted({_display_camera_id(item) for item in event.defects})
        self._ensure_cameras(camera_ids)

        for entry in self._camera_list:
            camera_id = str(entry["cameraId"])
            entry["live"] = camera_id in image_camera_ids or camera_id in camera_ids
            entry["frameVersion"] = int(entry.get("frameVersion", 0)) + (1 if entry["live"] else 0)
            if status == "NG" and camera_id == defect_camera_id:
                entry["status"] = "ng"
                entry["defectLabel"] = _defect_label(defect)
            elif status in {"RECHECK", "ERROR"}:
                entry["status"] = "warn"
                entry["defectLabel"] = event.message or status
            else:
                entry["status"] = "ok"
                entry["defectLabel"] = ""
        self.cameraListChanged.emit()

        self._record_stats(status, defect)
        self._append_log(event, status, defect)
        self._update_tact_rate()
        self._last_trigger_result = status
        self.lastTriggerResultChanged.emit()
        self._set_runtime_state(
            system_status="running",
            line_status="online",
            line_connected=True,
            trigger_error="" if event.error_code == 0 else event.message or f"error_code={event.error_code}",
        )

        if status == "NG" and defect is not None:
            self._show_ng_overlay(defect)

    def _ensure_cameras(self, camera_ids: list[str]) -> None:
        changed = False
        for camera_id in camera_ids:
            if camera_id in self._camera_index:
                continue
            entry = {
                "cameraId": camera_id,
                "live": False,
                "status": "ok",
                "defectLabel": "",
                "frameVersion": 0,
            }
            self._camera_list.append(entry)
            self._camera_index[camera_id] = entry
            changed = True
        if changed:
            self._camera_list.sort(key=lambda item: str(item["cameraId"]))

    def _record_stats(self, status: str, defect: DisplayDefect | None) -> None:
        self._stats.total += 1
        if status == "OK":
            self._stats.ok += 1
        elif status == "NG":
            self._stats.ng += 1
            label = _defect_label(defect)
            if label:
                self._stats.defect_types[label] = self._stats.defect_types.get(label, 0) + 1
        elif status == "ERROR":
            self._stats.error += 1
        else:
            self._stats.recheck += 1
        self._ok_count = self._stats.ok
        self._ng_count = self._stats.ng
        self.okCountChanged.emit()
        self.ngCountChanged.emit()
        self.statsChanged.emit()
        self.distributionChanged.emit()

    def _append_log(self, event: DisplayEvent, status: str, defect: DisplayDefect | None) -> None:
        timestamp = (event.timestamp_ms / 1000.0) if event.timestamp_ms else time.time()
        self._logs.insert(
            0,
            {
                "id": len(self._logs) + 1,
                "timestamp": timestamp,
                "camera_id": _display_camera_id(defect) if defect else "",
                "status": status,
                "reason": event.message,
                "defect_type": _defect_label(defect),
                "confidence": float(defect.score if defect else 0.0),
                "operator_action": "",
                "sequence_id": event.sequence_id,
                "trigger_id": event.trigger_id,
                "seat_id": event.seat_id,
            },
        )
        self._logs = self._logs[:500]
        self.logsChanged.emit()

    def _update_tact_rate(self) -> None:
        now = time.time()
        self._event_timestamps.append(now)
        self._event_timestamps = [item for item in self._event_timestamps if now - item <= 60.0]
        self._tact_rate = float(len(self._event_timestamps))
        self.tactRateChanged.emit()

    def _show_ng_overlay(self, defect: DisplayDefect) -> None:
        self._ng_defect_type = _defect_label(defect)
        self._ng_confidence = float(defect.score)
        self._ng_camera_id = _display_camera_id(defect)
        self._ng_image_version += 1
        self._ng_visible = True
        self._ng_started_at = time.time()
        self._remaining_seconds = self._ng_popup_seconds
        self.ngDefectTypeChanged.emit()
        self.ngConfidenceChanged.emit()
        self.ngCameraIdChanged.emit()
        self.ngImageVersionChanged.emit()
        self.ngOverlayVisibleChanged.emit()
        self.remainingSecondsChanged.emit()

    def _hide_ng_overlay(self, action: str) -> None:
        if not self._ng_visible:
            return
        if self._logs:
            self._logs[0]["operator_action"] = action
            self.logsChanged.emit()
        self._ng_visible = False
        self._remaining_seconds = 0
        self.ngOverlayVisibleChanged.emit()
        self.remainingSecondsChanged.emit()

    def _tick_ng_countdown(self) -> None:
        if not self._ng_visible:
            return
        remaining = max(0, self._ng_popup_seconds - int(time.time() - self._ng_started_at))
        if remaining != self._remaining_seconds:
            self._remaining_seconds = remaining
            self.remainingSecondsChanged.emit()
        if remaining <= 0:
            self._hide_ng_overlay("auto_confirm_defect")

    def _set_runtime_state(
        self,
        *,
        system_status: str,
        line_status: str,
        line_connected: bool,
        trigger_error: str,
    ) -> None:
        if self._system_status != system_status:
            self._system_status = system_status
            self.systemStatusChanged.emit()
        if self._line_status != line_status:
            self._line_status = line_status
            self.lineStatusChanged.emit()
        if self._line_connected != line_connected:
            self._line_connected = line_connected
            self.lineConnectedChanged.emit()
        self._set_trigger_error(trigger_error)

    def _set_trigger_error(self, message: str) -> None:
        if self._trigger_error == message:
            return
        self._trigger_error = message
        self.triggerErrorChanged.emit()
        self.triggerErrorDisplayChanged.emit()

    def _remove_review(self, record_id: int) -> None:
        self._reviews = [item for item in self._reviews if int(item.get("id", -1)) != int(record_id)]
        self.reviewsChanged.emit()


def _normal_status(decision: str) -> str:
    status = decision.upper()
    if status in {"OK", "NG", "ERROR"}:
        return status
    return "RECHECK"


def _primary_defect(defects: list[DisplayDefect]) -> DisplayDefect | None:
    if not defects:
        return None
    return max(defects, key=lambda item: float(item.score))


def _defect_label(defect: DisplayDefect | None) -> str:
    if defect is None:
        return ""
    return defect.class_name or defect.roi_name or defect.defect_id


def _display_camera_id(defect: DisplayDefect | None) -> str:
    if defect is None:
        return ""
    if defect.pose_id and defect.pose_id != defect.camera_id:
        return f"{defect.camera_id}/{defect.pose_id}"
    return defect.camera_id
