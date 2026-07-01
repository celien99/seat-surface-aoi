from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot

from display_app.services.display_bridge import ControllerEvent, DisplayBridge, DisplayDefect, DisplayEvent
from display_app.services.manual_trigger_client import ManualTriggerClient, ManualTriggerError
from display_app.services.operator_journal import OperatorJournal


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
    manualSnChanged = Signal()
    manualTriggerPendingChanged = Signal()
    manualTriggerStageChanged = Signal()
    manualTriggerFinished = Signal(bool, str, object)
    gridLayoutChanged = Signal()
    logsChanged = Signal()
    statsChanged = Signal()
    distributionChanged = Signal()
    reviewsChanged = Signal()
    recheckCountChanged = Signal()
    errorCountChanged = Signal()
    operationModeChanged = Signal()
    statusMessageChanged = Signal()
    stationAlarmChanged = Signal()

    def __init__(
        self,
        bridge: DisplayBridge,
        *,
        line_id: str = "AOI-1",
        grid_layout: str = "2x2",
        ng_popup_seconds: int = 30,
        manual_trigger_result_timeout_ms: int = 30000,
        journal: OperatorJournal | None = None,
        manual_trigger_client: ManualTriggerClient | None = None,
    ) -> None:
        super().__init__()
        self._bridge = bridge
        self._journal = journal
        self._manual_trigger_client = manual_trigger_client
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
        self._trigger_enabled = manual_trigger_client is not None
        self._manual_sn = ""
        self._operation_mode = "等待数据"
        self._status_message = "等待检测结果"
        self._station_alarm = ""
        self._camera_list: list[dict[str, Any]] = []
        self._camera_index: dict[str, dict[str, Any]] = {}
        self._last_event_key: tuple[int, int, int] | None = None
        self._last_controller_event_key: tuple[int, int, int] | None = None
        self._session_started_ms = int(time.time() * 1000)
        self._seen_detection_events: set[tuple[str, int, int, str, int]] = set()
        self._seen_controller_events: set[tuple[int, int, int]] = set()
        self._event_timestamps: list[float] = []
        persisted_logs = journal.load_logs() if journal is not None else []
        persisted_reviews = journal.load_reviews() if journal is not None else []
        self._logs: list[dict[str, Any]] = []
        self._reviews: list[dict[str, Any]] = list(persisted_reviews)
        self._stats = DisplayStats()
        for item in persisted_logs:
            if item.get("source") == "cpp_controller":
                try:
                    key = (
                        int(item.get("sequence_id", 0) or 0),
                        int(item.get("trigger_id", 0) or 0),
                        int(float(item.get("timestamp", 0.0) or 0.0) * 1_000_000),
                    )
                    self._seen_controller_events.add(key)
                except (TypeError, ValueError):
                    continue
            else:
                self._seen_detection_events.add(_detection_identity_from_log(item))
        self._status_filter = ""
        self._camera_filter = ""
        self._ng_popup_seconds = max(1, int(ng_popup_seconds))
        self._ng_started_at = 0.0
        self._manual_trigger_pending = False
        self._manual_trigger_stage = "idle"
        self._pending_manual_sn = ""
        self._manual_trigger_wait_started_at = 0.0
        self._manual_trigger_wait_started_ms = 0
        self._manual_trigger_result_timeout_s = max(1.0, float(manual_trigger_result_timeout_ms) / 1000.0)
        self.manualTriggerFinished.connect(self._finishManualTrigger)

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
        return self._trigger_enabled and not self._manual_trigger_pending

    def _get_manual_sn(self) -> str:
        return self._manual_sn

    def _get_manual_trigger_pending(self) -> bool:
        return self._manual_trigger_pending

    def _get_manual_trigger_stage(self) -> str:
        return self._manual_trigger_stage

    def _get_operation_mode(self) -> str:
        return self._operation_mode

    def _get_status_message(self) -> str:
        return self._status_message

    def _get_station_alarm(self) -> str:
        return self._station_alarm

    def _get_grid_layout(self) -> str:
        return self._grid_layout

    def _get_total(self) -> int:
        return self._stats.total

    def _get_ok(self) -> int:
        return self._stats.ok

    def _get_ng(self) -> int:
        return self._stats.ng

    def _get_recheck(self) -> int:
        return self._stats.recheck

    def _get_error(self) -> int:
        return self._stats.error

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
    manualSn = Property(str, _get_manual_sn, notify=manualSnChanged)
    manualTriggerPending = Property(bool, _get_manual_trigger_pending, notify=manualTriggerPendingChanged)
    manualTriggerStage = Property(str, _get_manual_trigger_stage, notify=manualTriggerStageChanged)
    operationMode = Property(str, _get_operation_mode, notify=operationModeChanged)
    statusMessage = Property(str, _get_status_message, notify=statusMessageChanged)
    stationAlarm = Property(str, _get_station_alarm, notify=stationAlarmChanged)
    gridLayout = Property(str, _get_grid_layout, notify=gridLayoutChanged)
    total = Property(int, _get_total, notify=statsChanged)
    ok = Property(int, _get_ok, notify=statsChanged)
    ng = Property(int, _get_ng, notify=statsChanged)
    recheck = Property(int, _get_recheck, notify=statsChanged)
    error = Property(int, _get_error, notify=statsChanged)
    okRate = Property(float, _get_ok_rate, notify=statsChanged)
    defectDistribution = Property(dict, _get_defect_distribution, notify=distributionChanged)
    logs = Property(list, _get_logs, notify=logsChanged)
    reviews = Property(list, _get_reviews, notify=reviewsChanged)

    @Slot()
    def pollLatest(self) -> None:
        self._poll_controller_events()
        detection_events = self._bridge.read_detection_events()
        if detection_events:
            for event in detection_events:
                self._process_detection_event(event)
            return
        event = self._bridge.read_latest()
        if event is None:
            if self._manual_trigger_pending:
                self._set_runtime_state(
                    system_status="running",
                    line_status="waiting_result",
                    line_connected=False,
                    trigger_error="",
                )
                self._set_status_message(self._manual_trigger_status_message())
                return
            self._set_runtime_state(
                system_status="paused",
                line_status="waiting",
                line_connected=False,
                trigger_error="等待检测结果 display_latest.json",
            )
            self._set_status_message("等待 Python detector 展示事件")
            return
        self._process_detection_event(event, from_latest=True)

    @Slot()
    def refreshTriggerState(self) -> None:
        self.pollLatest()
        self._tick_manual_trigger_wait()
        self._tick_ng_countdown()

    @Slot()
    def acknowledgeNG(self) -> None:
        self._hide_ng_overlay("confirm_defect")

    @Slot()
    def markReview(self) -> None:
        if self._logs:
            review = dict(self._logs[0])
            review["id"] = len(self._reviews) + 1
            review["review_status"] = "pending"
            self._reviews.insert(0, review)
            self._save_reviews()
            self._persist_action("mark_review", review)
            self.reviewsChanged.emit()
        self._hide_ng_overlay("mark_review")

    @Slot()
    def dismissFalseAlarm(self) -> None:
        self._hide_ng_overlay("false_alarm")

    @Slot()
    def manualTrigger(self) -> None:
        if self._manual_trigger_client is None:
            self._set_trigger_error("当前展示程序只读检测结果，手动触发未启用")
            return
        self.submitManualTrigger(self._manual_sn)

    @Slot(str)
    def setManualSn(self, value: str) -> None:
        if self._manual_sn == value:
            return
        self._manual_sn = value
        self.manualSnChanged.emit()

    @Slot(str)
    def submitManualTrigger(self, sn: str) -> None:
        self.setManualSn(sn)
        if self._manual_trigger_client is None:
            self._set_trigger_error("当前展示程序只读检测结果，手动触发未启用")
            return
        if self._manual_trigger_pending:
            self._set_trigger_error("上一条手动触发仍在处理中")
            return
        self._set_manual_trigger_state(pending=True, stage="submitting")
        self._set_trigger_error("")
        self._set_status_message("正在提交手动触发")
        if self._last_trigger_result:
            self._last_trigger_result = ""
            self.lastTriggerResultChanged.emit()

        client = self._manual_trigger_client

        def worker() -> None:
            try:
                result = client.trigger(sn)
            except ManualTriggerError as exc:
                self.manualTriggerFinished.emit(False, str(exc), {})
            except Exception as exc:
                self.manualTriggerFinished.emit(False, f"手动触发异常: {exc}", {})
            else:
                self.manualTriggerFinished.emit(
                    True,
                    f"手动触发已提交 SN={result.sn} ({result.elapsed_ms:.0f} ms)",
                    {
                        "source": "display_app",
                        "seat_id": result.sn,
                        "sn": result.sn,
                        "host": result.host,
                        "port": result.port,
                        "elapsed_ms": result.elapsed_ms,
                    },
                )

        thread = threading.Thread(target=worker, name="display-manual-trigger", daemon=True)
        thread.start()

    @Slot(bool, str, object)
    def _finishManualTrigger(self, success: bool, message: str, payload: object) -> None:
        if success:
            self._set_trigger_error("")
            if isinstance(payload, dict):
                self._persist_action("manual_trigger", payload)
                self._pending_manual_sn = str(payload.get("sn", "") or payload.get("seat_id", "") or self._manual_sn).strip()
            else:
                self._pending_manual_sn = self._manual_sn.strip()
            if self._pending_manual_sn and self._manual_sn != self._pending_manual_sn:
                self._manual_sn = self._pending_manual_sn
                self.manualSnChanged.emit()
            self._manual_trigger_wait_started_at = time.monotonic()
            self._manual_trigger_wait_started_ms = int(time.time() * 1000)
            self._set_manual_trigger_state(pending=True, stage="waiting_result")
            self._set_status_message(f"{message}，等待检测结果")
        else:
            self._set_trigger_error(message)
            self._set_status_message("手动触发提交失败")
            self._pending_manual_sn = ""
            self._manual_trigger_wait_started_at = 0.0
            self._manual_trigger_wait_started_ms = 0
            self._set_manual_trigger_state(pending=False, stage="idle")

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
        review = self._review_by_id(record_id)
        if review is not None:
            self._persist_action("review_confirm_defect", review)
        self._remove_review(record_id)

    @Slot(int)
    def dismissAsOK(self, record_id: int) -> None:
        review = self._review_by_id(record_id)
        if review is not None:
            self._persist_action("review_dismiss_as_ok", review)
        self._remove_review(record_id)

    def _process_detection_event(self, event: DisplayEvent, *, from_latest: bool = False) -> None:
        if event.event_key == self._last_event_key and from_latest:
            return
        event_identity = _display_event_identity(event)
        is_current = _display_event_is_current_session(event, self._session_started_ms)
        should_record = event_identity not in self._seen_detection_events and is_current
        if not from_latest or is_current:
            self._seen_detection_events.add(event_identity)
        self._last_event_key = event.event_key
        image_report = self._bridge.publish_images_report(event)
        self._apply_event(
            event,
            image_report.successful_camera_ids,
            failed_image_camera_ids=image_report.failed_camera_ids,
            should_record=should_record,
            allow_operator_actions=should_record,
        )

    def _apply_event(
        self,
        event: DisplayEvent,
        image_camera_ids: list[str],
        *,
        failed_image_camera_ids: list[str] | None = None,
        should_record: bool = True,
        allow_operator_actions: bool = True,
    ) -> None:
        status = _normal_status(event.decision)
        defect = _primary_defect(event.defects)
        defect_camera_id = _display_camera_id(defect) if defect else ""
        failed_image_camera_ids = failed_image_camera_ids or []
        camera_ids = sorted(
            set(image_camera_ids + failed_image_camera_ids + [_display_camera_id(item) for item in event.defects if item.camera_id])
        )
        if not camera_ids and event.defects:
            camera_ids = sorted({_display_camera_id(item) for item in event.defects})
        self._ensure_cameras(camera_ids)

        # ROI 未识别到目标物体属于信息性提示，不应触发告警或复检流程
        is_target_issue = _is_target_detection_issue(event)

        for entry in self._camera_list:
            camera_id = str(entry["cameraId"])
            has_new_image = camera_id in image_camera_ids
            has_failed_image = camera_id in failed_image_camera_ids
            entry["live"] = has_new_image
            entry["frameVersion"] = int(entry.get("frameVersion", 0)) + (1 if has_new_image or has_failed_image else 0)
            if status == "NG" and camera_id == defect_camera_id:
                entry["status"] = "ng"
                entry["defectLabel"] = _defect_label(defect)
            elif has_failed_image:
                entry["status"] = "warn"
                entry["defectLabel"] = "图像加载失败"
            elif is_target_issue:
                entry["status"] = "warn"
                entry["defectLabel"] = event.message or "未识别到目标物体"
            elif status == "ERROR":
                entry["status"] = "error"
                entry["defectLabel"] = event.message or status
            elif status == "RECHECK":
                entry["status"] = "warn"
                entry["defectLabel"] = event.message or status
            else:
                entry["status"] = "ok"
                entry["defectLabel"] = ""
        self.cameraListChanged.emit()

        if should_record:
            self._record_stats(status, defect)
            self._append_detection_log(event, status, defect)
            self._update_tact_rate()
        self._last_trigger_result = status
        self.lastTriggerResultChanged.emit()
        self._set_operation_mode(_operation_mode(event, status))
        if is_target_issue:
            self._set_status_message(event.message or "未识别到目标物体")
        else:
            self._set_status_message(_event_status_message(event, status))
        self._set_runtime_state(
            system_status="running",
            line_status="online",
            line_connected=True,
            trigger_error="" if (event.error_code == 0 or is_target_issue) else event.message or f"error_code={event.error_code}",
        )

        if allow_operator_actions and status == "NG" and defect is not None:
            self._show_ng_overlay(defect)
        self._complete_manual_trigger_wait(event)

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
        self.recheckCountChanged.emit()
        self.errorCountChanged.emit()
        self.statsChanged.emit()
        self.distributionChanged.emit()

    def _append_detection_log(self, event: DisplayEvent, status: str, defect: DisplayDefect | None) -> None:
        timestamp = (event.timestamp_ms / 1000.0) if event.timestamp_ms else time.time()
        is_target_issue = _is_target_detection_issue(event)
        reason = (event.message or "未识别到目标物体") if is_target_issue else _event_status_message(event, status)
        record = {
            "id": self._next_log_id(),
            "timestamp": timestamp,
            "source": event.source,
            "camera_id": _display_camera_id(defect) if defect else "",
            "status": status,
            "reason": reason,
            "defect_type": _defect_label(defect),
            "confidence": float(defect.score if defect else 0.0),
            "operator_action": "",
            "sequence_id": event.sequence_id,
            "trigger_id": event.trigger_id,
            "seat_id": event.seat_id,
            "error_code": event.error_code,
            "trace_dir": event.trace_dir,
        }
        self._logs.insert(0, record)
        self._logs = self._logs[:500]
        if self._journal is not None:
            self._journal.append_log(record)
        self.logsChanged.emit()

    def _append_controller_log(self, event: ControllerEvent) -> None:
        status = _normal_status(event.decision)
        timestamp = event.timestamp_us / 1_000_000.0 if event.timestamp_us else time.time()
        record = {
            "id": self._next_log_id(),
            "timestamp": timestamp,
            "source": "cpp_controller",
            "camera_id": "",
            "status": status,
            "reason": event.message or event.error or event.event,
            "defect_type": event.error,
            "confidence": 0.0,
            "operator_action": "",
            "sequence_id": event.sequence_id,
            "trigger_id": event.trigger_id,
            "seat_id": event.seat_id,
            "error_code": event.error_code,
            "station_state": event.station_state,
            "alarm_level": event.alarm_level,
        }
        self._logs.insert(0, record)
        self._logs = self._logs[:500]
        if self._journal is not None:
            self._journal.append_log(record)
        self.logsChanged.emit()

    def _poll_controller_events(self) -> None:
        for event in self._bridge.read_controller_events():
            if event.event_key == self._last_controller_event_key or event.event_key in self._seen_controller_events:
                continue
            self._last_controller_event_key = event.event_key
            self._seen_controller_events.add(event.event_key)
            if _controller_event_is_current_session(event, self._session_started_ms) and _is_controller_alert(event):
                self._append_controller_log(event)
                self._set_station_alarm(event.message or event.error)
                self._set_status_message(event.message or event.error or event.event)
                self._set_runtime_state(
                    system_status="running" if event.station_state != "Fault" else "paused",
                    line_status=event.station_state or "controller",
                    line_connected=True,
                    trigger_error=event.message if event.error_code != 0 else "",
                )

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
            self._persist_action(action, self._logs[0])
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

    def _set_manual_trigger_state(self, *, pending: bool, stage: str) -> None:
        line_busy_changed = self._line_busy != pending
        pending_changed = self._manual_trigger_pending != pending
        stage_changed = self._manual_trigger_stage != stage
        self._line_busy = pending
        self._manual_trigger_pending = pending
        self._manual_trigger_stage = stage
        if line_busy_changed:
            self.lineBusyChanged.emit()
        if pending_changed:
            self.manualTriggerPendingChanged.emit()
            self.triggerEnabledChanged.emit()
        if stage_changed:
            self.manualTriggerStageChanged.emit()

    def _manual_trigger_status_message(self) -> str:
        if self._manual_trigger_stage == "waiting_result":
            if self._pending_manual_sn:
                return f"手动触发已提交 SN={self._pending_manual_sn}，等待检测结果"
            return "手动触发已提交，等待检测结果"
        if self._manual_trigger_stage == "submitting":
            return "正在提交手动触发"
        return self._status_message

    def _tick_manual_trigger_wait(self) -> None:
        if not self._manual_trigger_pending or self._manual_trigger_stage != "waiting_result":
            return
        if self._manual_trigger_wait_started_at <= 0:
            self._manual_trigger_wait_started_at = time.monotonic()
        elapsed_s = time.monotonic() - self._manual_trigger_wait_started_at
        if elapsed_s < self._manual_trigger_result_timeout_s:
            return
        self._set_trigger_error("手动触发已提交，但等待检测结果超时")
        self._set_status_message("手动触发等待检测结果超时，可确认链路状态后重新触发")
        self._pending_manual_sn = ""
        self._manual_trigger_wait_started_at = 0.0
        self._manual_trigger_wait_started_ms = 0
        self._set_manual_trigger_state(pending=False, stage="idle")

    def _complete_manual_trigger_wait(self, event: DisplayEvent) -> None:
        if not self._manual_trigger_pending or self._manual_trigger_stage != "waiting_result":
            return
        event_after_submit = (
            self._manual_trigger_wait_started_ms <= 0
            or event.timestamp_ms <= 0
            or event.timestamp_ms >= self._manual_trigger_wait_started_ms
        )
        event_matches_sn = (
            not self._pending_manual_sn
            or event.seat_id == self._pending_manual_sn
            or event.seat_id.endswith("_" + self._pending_manual_sn)
        )
        if not event_after_submit or not event_matches_sn:
            return
        status = _normal_status(event.decision)
        self._pending_manual_sn = ""
        self._manual_trigger_wait_started_at = 0.0
        self._manual_trigger_wait_started_ms = 0
        self._set_manual_trigger_state(pending=False, stage="idle")
        # 收到对应检测结果后再清空 SN，避免操作者在等待期间误以为可以扫下一件。
        if self._manual_sn:
            self._manual_sn = ""
            self.manualSnChanged.emit()
        self._set_status_message(f"手动触发完成，检测结果 {status}")

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
        self._save_reviews()
        self.reviewsChanged.emit()

    def _review_by_id(self, record_id: int) -> dict[str, Any] | None:
        for item in self._reviews:
            if int(item.get("id", -1)) == int(record_id):
                return item
        return None

    def _save_reviews(self) -> None:
        if self._journal is not None:
            self._journal.save_reviews(self._reviews)

    def _persist_action(self, action: str, record: dict[str, Any]) -> None:
        if self._journal is not None:
            payload = dict(record)
            payload["operator_action"] = action
            payload["action_timestamp"] = time.time()
            self._journal.append_action(payload)

    def _next_log_id(self) -> int:
        max_id = 0
        for item in self._logs:
            try:
                max_id = max(max_id, int(item.get("id", 0)))
            except (TypeError, ValueError):
                continue
        return max_id + 1

    def _set_operation_mode(self, value: str) -> None:
        if self._operation_mode == value:
            return
        self._operation_mode = value
        self.operationModeChanged.emit()

    def _set_status_message(self, value: str) -> None:
        if self._status_message == value:
            return
        self._status_message = value
        self.statusMessageChanged.emit()

    def _set_station_alarm(self, value: str) -> None:
        if self._station_alarm == value:
            return
        self._station_alarm = value
        self.stationAlarmChanged.emit()


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


def _operation_mode(event: DisplayEvent, status: str) -> str:
    if event.asset_unavailable or event.sample_collection:
        return "采样模式"
    if status in {"RECHECK", "ERROR"}:
        return "复检/异常"
    return "在线检测"


def _event_status_message(event: DisplayEvent, status: str) -> str:
    if event.asset_unavailable or event.sample_collection:
        return event.message or "模型资产未就绪，当前任务保存为训练样本"
    if event.message:
        return event.message
    if status == "RECHECK":
        return "当前结果需要复检"
    if status == "ERROR":
        return "检测链路异常"
    return ""


def _display_event_identity(event: DisplayEvent) -> tuple[str, int, int, str, int]:
    return (
        event.source,
        int(event.sequence_id),
        int(event.trigger_id),
        event.seat_id,
        int(event.timestamp_ms),
    )


def _detection_identity_from_log(item: dict[str, Any]) -> tuple[str, int, int, str, int]:
    timestamp_ms = 0
    try:
        timestamp_ms = int(float(item.get("timestamp", 0.0) or 0.0) * 1000)
    except (TypeError, ValueError):
        timestamp_ms = 0
    try:
        sequence_id = int(item.get("sequence_id", 0) or 0)
    except (TypeError, ValueError):
        sequence_id = 0
    try:
        trigger_id = int(item.get("trigger_id", 0) or 0)
    except (TypeError, ValueError):
        trigger_id = 0
    return (
        str(item.get("source", "python_detector") or "python_detector"),
        sequence_id,
        trigger_id,
        str(item.get("seat_id", "") or ""),
        timestamp_ms,
    )


def _display_event_is_current_session(event: DisplayEvent, session_started_ms: int) -> bool:
    if event.timestamp_ms <= 0:
        return True
    return event.timestamp_ms >= session_started_ms - 2000


def _controller_event_is_current_session(event: ControllerEvent, session_started_ms: int) -> bool:
    if event.timestamp_us <= 0:
        return True
    return event.timestamp_us >= (session_started_ms - 2000) * 1000


def _is_controller_alert(event: ControllerEvent) -> bool:
    if event.error_code != 0:
        return True
    return event.event in {
        "inspection_recheck",
        "recheck_output_failed",
        "signal_result_publish_failed",
        "capture_only_result_publish_failed",
    }


# Python 检测器返回的消息中，匹配以下任一模式即判定为"ROI 未识别到目标物体"，
# 应展示为信息性提示而非复检/告警。
_TARGET_DETECTION_ISSUE_PATTERNS = (
    "未识别到目标",
    "目标未检测到",
    "目标未识别",
    "目标丢失",
    "未检测到目标",
    "无目标",
    "ROI未匹配",
    "ROI无结果",
    "未匹配到目标",
    "目标不存在",
    "检测目标失败",
    "target not found",
    "no target detected",
)


def _is_target_detection_issue(event: DisplayEvent) -> bool:
    """判定检测器返回的 ERROR/RECHECK 是否属于"ROI 未发现目标"信息性提示。"""
    if not event.message:
        return False
    message_lower = event.message.lower()
    return any(p.lower() in message_lower for p in _TARGET_DETECTION_ISSUE_PATTERNS)


def _stats_from_logs(logs: list[dict[str, Any]]) -> DisplayStats:
    stats = DisplayStats()
    for item in reversed(logs):
        status = str(item.get("status", "")).upper()
        if status not in {"OK", "NG", "RECHECK", "ERROR"}:
            continue
        stats.total += 1
        if status == "OK":
            stats.ok += 1
        elif status == "NG":
            stats.ng += 1
            defect_type = str(item.get("defect_type", "") or "")
            if defect_type:
                stats.defect_types[defect_type] = stats.defect_types.get(defect_type, 0) + 1
        elif status == "ERROR":
            stats.error += 1
        else:
            stats.recheck += 1
    return stats
