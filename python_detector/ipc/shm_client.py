from __future__ import annotations

import mmap
import struct
import time
from dataclasses import dataclass

from .data_types import CameraBundle, DefectResult, InspectionResult, LightFrame, SeatInspectionJob
from .shared_memory_map import SharedMemoryMap
from .shm_protocol import (
    DEFAULT_FRAME_SLOT_SIZE,
    DEFAULT_RESULT_SLOT_SIZE,
    DEFAULT_SLOT_COUNT,
    DTypeCode,
    ErrorCode,
    InspectionDecision,
    FRAME_SHM_NAME,
    FRAME_SLOT_HEADER_PREFIX,
    FRAME_SLOT_HEADER_SIZE,
    INSPECTION_RESULT_META,
    LIGHT_FRAME_META,
    MAX_EVIDENCE_LIGHTS,
    MAX_DEFECTS_PER_RESULT,
    PixelFormat,
    RESULT_SHM_NAME,
    RESULT_SLOT_HEADER_PREFIX,
    RESULT_SLOT_HEADER_SIZE,
    SEAT_JOB_META,
    SHM_HEADER,
    SHM_PROTOCOL_MAGIC,
    SHM_PROTOCOL_VERSION,
    SlotState,
    AtomicU32,
    assert_protocol_layout,
    crc32,
    decode_cstr,
    encode_cstr,
    ColorOrder,
    MAX_FRAMES_PER_JOB,
    frame_slot_meta_offset,
    frame_slot_image_offset,
    result_slot_defects_offset,
)


LIGHT_ID_BY_INDEX = {
    1: "DIFFUSE",
    2: "POLAR_DIFFUSE",
    3: "HIGH_LEFT",
    4: "HIGH_RIGHT",
    5: "HIGH_FRONT",
    6: "HIGH_REAR",
    7: "LOW_LEFT",
    8: "LOW_RIGHT",
    9: "LOW_FRONT",
    10: "LOW_REAR",
    11: "NIR",
    12: "DOME_ROI",
}

CAMERA_ID_BY_INDEX = {
    0: "TOP_BACK",
    1: "TOP_CUSHION",
    2: "LEFT",
    3: "RIGHT",
}
LIGHT_INDEX_BY_ID = {light_id: index for index, light_id in LIGHT_ID_BY_INDEX.items()}
CAMERA_INDEX_BY_ID = {camera_id: index for index, camera_id in CAMERA_ID_BY_INDEX.items()}


@dataclass(frozen=True)
class _FrameSlotIdentity:
    sequence_id: int
    trigger_id: int
    seat_id: str


class _FrameSlotReadError(RuntimeError):
    def __init__(self, message: str, error_code: ErrorCode) -> None:
        super().__init__(message)
        self.error_code = error_code


class ShmClient:
    def __init__(
        self,
        frame_name: str = FRAME_SHM_NAME,
        result_name: str = RESULT_SHM_NAME,
        slot_count: int = DEFAULT_SLOT_COUNT,
        frame_slot_size: int = DEFAULT_FRAME_SLOT_SIZE,
        result_slot_size: int = DEFAULT_RESULT_SLOT_SIZE,
    ) -> None:
        assert_protocol_layout()
        self.slot_count = slot_count
        self.frame_slot_size = frame_slot_size
        self.result_slot_size = result_slot_size
        frame_total = SHM_HEADER.size + slot_count * frame_slot_size
        result_total = SHM_HEADER.size + slot_count * result_slot_size
        self.frames = SharedMemoryMap.open(frame_name, frame_total)
        self.results = SharedMemoryMap.open(result_name, result_total)
        self._validate_header(self.frames.mm, frame_slot_size)
        self._validate_header(self.results.mm, result_slot_size)
        self._pending_frame_slots: dict[int, int] = {}
        self._camera_index_by_id = dict(CAMERA_INDEX_BY_ID)
        self._camera_index_by_view: dict[tuple[str, str], int] = {}

    def close(self) -> None:
        self.frames.close()
        self.results.close()

    def wait_next_job(self, timeout_ms: int) -> SeatInspectionJob | None:
        deadline = time.monotonic() + timeout_ms / 1000.0
        while time.monotonic() < deadline:
            for slot_index in range(self.slot_count):
                base = self._slot_base(slot_index, self.frame_slot_size)
                if AtomicU32.load(self.frames.mm, base) != SlotState.READY:
                    continue
                job = self._read_frame_slot(slot_index)
                if job is not None:
                    self._pending_frame_slots[job.sequence_id] = slot_index
                    return job
            time.sleep(0.002)
        return None

    def publish_result(self, result: InspectionResult) -> None:
        defects = result.defects[:MAX_DEFECTS_PER_RESULT]
        payload_size = RESULT_SLOT_HEADER_SIZE + len(defects) * struct.calcsize("<64s64s64sI64s64s64s4ifII8iqII")

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            for slot_index in range(self.slot_count):
                base = self._slot_base(slot_index, self.result_slot_size)
                if AtomicU32.load(self.results.mm, base) != SlotState.EMPTY:
                    continue
                AtomicU32.store(self.results.mm, base, SlotState.WRITING)
                try:
                    self._write_result_slot(base, result, defects, payload_size)
                except Exception:
                    AtomicU32.store(self.results.mm, base, SlotState.CORRUPTED)
                    self._release_frame_slot(result.sequence_id)
                    raise
                AtomicU32.store(self.results.mm, base, SlotState.READY)
                self._release_frame_slot(result.sequence_id)
                return
            time.sleep(0.002)
        self._release_frame_slot(result.sequence_id)
        raise TimeoutError("result slot unavailable before timeout")

    def _validate_header(self, mm: mmap.mmap, expected_slot_size: int) -> None:
        magic, version, slot_count, slot_size, _write, _read, _heartbeat = SHM_HEADER.unpack_from(mm, 0)
        if magic != SHM_PROTOCOL_MAGIC or version != SHM_PROTOCOL_VERSION:
            raise RuntimeError(f"shared memory protocol mismatch: magic={magic:x} version={version}")
        if slot_count != self.slot_count or slot_size != expected_slot_size:
            raise RuntimeError(f"shared memory layout mismatch: slots={slot_count} slot_size={slot_size}")

    def _slot_base(self, slot_index: int, slot_size: int) -> int:
        return SHM_HEADER.size + slot_index * slot_size

    def _read_frame_slot(self, slot_index: int) -> SeatInspectionJob | None:
        base = self._slot_base(slot_index, self.frame_slot_size)
        AtomicU32.store(self.frames.mm, base, SlotState.READING)
        identity: _FrameSlotIdentity | None = None
        try:
            prefix = FRAME_SLOT_HEADER_PREFIX.unpack_from(self.frames.mm, base)
            _state, sequence_id, payload_size, header_crc32, payload_crc32, frame_meta_count, _reserved = prefix

            self._validate_frame_header_crc(base, header_crc32)

            job_offset = base + FRAME_SLOT_HEADER_PREFIX.size
            job_values = SEAT_JOB_META.unpack_from(self.frames.mm, job_offset)
            (
                job_sequence_id,
                trigger_id,
                seat_id_raw,
                sku_raw,
                recipe_id_raw,
                view_count,
                frame_count,
                _capture_mode,
                _job_reserved,
                _created_at_us,
            ) = job_values
            identity = _FrameSlotIdentity(
                sequence_id=sequence_id,
                trigger_id=trigger_id,
                seat_id=decode_cstr(seat_id_raw),
            )
            if sequence_id != job_sequence_id or frame_count != frame_meta_count:
                raise _FrameSlotReadError(
                    "frame slot sequence or frame count mismatch",
                    ErrorCode.INVALID_PAYLOAD,
                )

            self._validate_frame_payload_bounds(payload_size, frame_meta_count)
            payload = memoryview(self.frames.mm)[base + frame_slot_meta_offset() : base + payload_size]
            if crc32(payload) != payload_crc32:
                raise _FrameSlotReadError("frame payload CRC mismatch", ErrorCode.CRC_MISMATCH)

            bundles: dict[tuple[str, str], CameraBundle] = {}
            image_ranges: list[tuple[int, int]] = []
            image_region_offset = frame_slot_image_offset(frame_meta_count)
            meta_offset = base + frame_slot_meta_offset()
            for i in range(frame_meta_count):
                values = LIGHT_FRAME_META.unpack_from(self.frames.mm, meta_offset + i * LIGHT_FRAME_META.size)
                frame = self._make_light_frame(values, base, payload_size, image_region_offset, image_ranges)
                bundle_key = (frame.camera_id, frame.pose_id)
                if bundle_key not in bundles:
                    bundles[bundle_key] = CameraBundle(
                        camera_id=frame.camera_id,
                        pose_id=frame.pose_id,
                        light_frames={},
                    )
                if frame.light_id in bundles[bundle_key].light_frames:
                    raise _FrameSlotReadError(
                        f"duplicate frame for camera/pose/light: {frame.camera_id}/{frame.pose_id}/{frame.light_id}",
                        ErrorCode.INVALID_PAYLOAD,
                    )
                bundles[bundle_key].light_frames[frame.light_id] = frame

            job = SeatInspectionJob(
                sequence_id=sequence_id,
                trigger_id=trigger_id,
                seat_id=decode_cstr(seat_id_raw),
                recipe_id=decode_cstr(recipe_id_raw),
                sku=decode_cstr(sku_raw),
                camera_bundles=list(bundles.values()),
            )
            if len(job.camera_bundles) != view_count:
                raise _FrameSlotReadError("frame slot view count mismatch", ErrorCode.INVALID_PAYLOAD)
            return job
        except _FrameSlotReadError as exc:
            if identity is None:
                AtomicU32.store(self.frames.mm, base, SlotState.EMPTY)
            else:
                self._publish_frame_slot_error(slot_index, identity, exc.error_code)
            return None
        except Exception:
            if identity is None:
                AtomicU32.store(self.frames.mm, base, SlotState.EMPTY)
            else:
                self._publish_frame_slot_error(slot_index, identity, ErrorCode.INTERNAL_ERROR)
            return None

    def _validate_frame_header_crc(self, base: int, header_crc32: int) -> None:
        header_bytes = bytearray(self.frames.mm[base : base + FRAME_SLOT_HEADER_SIZE])
        struct.pack_into("<I", header_bytes, 0, 0)
        struct.pack_into("<I", header_bytes, 20, 0)
        if crc32(header_bytes) != header_crc32:
            raise _FrameSlotReadError("frame header CRC mismatch", ErrorCode.CRC_MISMATCH)

    def _validate_frame_payload_bounds(self, payload_size: int, frame_meta_count: int) -> None:
        min_payload_size = frame_slot_image_offset(frame_meta_count)
        if (
            payload_size < min_payload_size
            or payload_size > self.frame_slot_size
            or frame_meta_count <= 0
            or frame_meta_count > MAX_FRAMES_PER_JOB
        ):
            raise _FrameSlotReadError("invalid frame payload size or frame count", ErrorCode.INVALID_PAYLOAD)

    def _publish_frame_slot_error(
        self,
        slot_index: int,
        identity: _FrameSlotIdentity,
        error_code: ErrorCode,
    ) -> None:
        self._pending_frame_slots[identity.sequence_id] = slot_index
        result = InspectionResult(
            sequence_id=identity.sequence_id,
            trigger_id=identity.trigger_id,
            seat_id=identity.seat_id,
            decision="ERROR",
            defects=[],
            quality_pass=False,
            error_code=int(error_code),
            elapsed_ms=0.0,
        )
        try:
            self.publish_result(result)
        except Exception:
            self._release_frame_slot(identity.sequence_id)

    def _make_light_frame(
        self,
        values: tuple,
        slot_base: int,
        payload_size: int,
        image_region_offset: int,
        image_ranges: list[tuple[int, int]],
    ) -> LightFrame:
        (
            camera_index,
            pose_index,
            light_index,
            frame_index,
            light_seq_index,
            width,
            height,
            channels,
            stride_bytes,
            pixel_format,
            bit_depth,
            color_order,
            dtype_code,
            timestamp_us,
            shot_id,
            robot_timestamp_us,
            exposure_us,
            gain,
            robot_x_mm,
            robot_y_mm,
            robot_z_mm,
            robot_roll_deg,
            robot_pitch_deg,
            robot_yaw_deg,
            camera_id_raw,
            pose_id_raw,
            calibration_id_raw,
            image_offset,
            image_size,
            image_crc32,
            _reserved,
        ) = values
        image_start = slot_base + image_offset
        image_end = image_start + image_size
        min_image_size = int(stride_bytes) * int(height)
        image_range = (int(image_offset), int(image_offset + image_size))
        if (
            image_offset < image_region_offset
            or image_size < min_image_size
            or image_size <= 0
            or image_offset + image_size > payload_size
        ):
            raise _FrameSlotReadError("frame image range exceeds payload", ErrorCode.INVALID_PAYLOAD)
        for existing_start, existing_end in image_ranges:
            if image_range[0] < existing_end and image_range[1] > existing_start:
                raise _FrameSlotReadError("frame image ranges overlap", ErrorCode.INVALID_PAYLOAD)
        image_ranges.append(image_range)
        image = memoryview(self.frames.mm)[image_start:image_end]
        if crc32(image) != image_crc32:
            raise _FrameSlotReadError("frame image CRC mismatch", ErrorCode.CRC_MISMATCH)
        try:
            pixel_format_name = PixelFormat(pixel_format).name
            color_order_name = ColorOrder(color_order).name
            dtype_name = DTypeCode(dtype_code).name
        except ValueError as exc:
            raise _FrameSlotReadError("unknown frame metadata enum value", ErrorCode.INVALID_PAYLOAD) from exc
        camera_id = decode_cstr(camera_id_raw) or CAMERA_ID_BY_INDEX.get(camera_index, f"CAMERA_{camera_index}")
        pose_id = decode_cstr(pose_id_raw) or f"POSE_{pose_index}"
        self._remember_camera_index(camera_id, pose_id, camera_index)
        return LightFrame(
            camera_id=camera_id,
            pose_id=pose_id,
            light_id=LIGHT_ID_BY_INDEX.get(light_index, f"LIGHT_{light_index}"),
            frame_index=frame_index,
            light_seq_index=light_seq_index,
            width=width,
            height=height,
            channels=channels,
            stride_bytes=stride_bytes,
            pixel_format=pixel_format_name,
            bit_depth=bit_depth,
            color_order=color_order_name,
            dtype=dtype_name,
            timestamp_us=timestamp_us,
            shot_id=shot_id,
            robot_timestamp_us=robot_timestamp_us,
            robot_tcp_xyz_mm=(robot_x_mm, robot_y_mm, robot_z_mm),
            robot_rpy_deg=(robot_roll_deg, robot_pitch_deg, robot_yaw_deg),
            exposure_us=exposure_us,
            gain=gain,
            calibration_id=decode_cstr(calibration_id_raw),
            image_crc32=image_crc32,
            image=image,
        )

    def _write_result_slot(
        self,
        base: int,
        result: InspectionResult,
        defects: list[DefectResult],
        payload_size: int,
    ) -> None:
        decision = InspectionDecision[result.decision].value
        result_meta = INSPECTION_RESULT_META.pack(
            result.sequence_id,
            result.trigger_id,
            encode_cstr(result.seat_id),
            decision,
            len(defects),
            1 if result.quality_pass else 0,
            result.error_code,
            float(result.elapsed_ms),
            0,
        )
        defect_bytes = b"".join(self._pack_defect(defect) for defect in defects)
        payload_crc = crc32(defect_bytes)
        RESULT_SLOT_HEADER_PREFIX.pack_into(
            self.results.mm,
            base,
            SlotState.WRITING,
            result.sequence_id,
            payload_size,
            0,
            payload_crc,
            len(defects),
            0,
        )
        self.results.mm[base + RESULT_SLOT_HEADER_PREFIX.size : base + RESULT_SLOT_HEADER_SIZE] = result_meta
        self.results.mm[
            base + result_slot_defects_offset() : base + result_slot_defects_offset() + len(defect_bytes)
        ] = defect_bytes

        header_bytes = bytearray(self.results.mm[base : base + RESULT_SLOT_HEADER_SIZE])
        struct.pack_into("<I", header_bytes, 0, 0)
        struct.pack_into("<I", header_bytes, 20, 0)
        header_crc = crc32(header_bytes)
        struct.pack_into("<I", self.results.mm, base + 20, header_crc)

    def _pack_defect(self, defect: DefectResult) -> bytes:
        bbox = tuple(int(v) for v in defect.bbox_xyxy_pixel)
        if len(defect.evidence_lights) > MAX_EVIDENCE_LIGHTS:
            raise ValueError(
                f"too many evidence_lights for defect {defect.defect_id}: "
                f"{len(defect.evidence_lights)} > {MAX_EVIDENCE_LIGHTS}"
            )
        evidence = [self._light_index(light) for light in defect.evidence_lights]
        evidence = (evidence + [0] * MAX_EVIDENCE_LIGHTS)[:MAX_EVIDENCE_LIGHTS]
        return struct.pack(
            "<64s64s64sI64s64s64s4ifII8iqII",
            encode_cstr(defect.defect_id),
            encode_cstr(defect.class_name),
            encode_cstr(defect.severity),
            self._camera_index_for_defect(defect),
            encode_cstr(defect.camera_id),
            encode_cstr(defect.pose_id),
            encode_cstr(defect.roi_name),
            *bbox,
            float(defect.score),
            int(defect.area_px),
            min(len(defect.evidence_lights), 8),
            *evidence,
            -1 if defect.mask_offset is None else int(defect.mask_offset),
            InspectionDecision[defect.decision].value,
            0,
        )

    def _camera_index(self, camera_id: str) -> int:
        camera_index_by_id = getattr(self, "_camera_index_by_id", CAMERA_INDEX_BY_ID)
        if camera_id in camera_index_by_id:
            return camera_index_by_id[camera_id]
        if camera_id.startswith("CAMERA_"):
            suffix = camera_id.removeprefix("CAMERA_")
            if suffix.isdigit():
                return int(suffix)
        raise ValueError(f"unknown camera_id for result serialization: {camera_id}")

    def _camera_index_for_defect(self, defect: DefectResult) -> int:
        camera_index_by_view = getattr(self, "_camera_index_by_view", {})
        pose_id = defect.pose_id or defect.camera_id
        view_key = (defect.camera_id, pose_id)
        if view_key in camera_index_by_view:
            return camera_index_by_view[view_key]
        return self._camera_index(defect.camera_id)

    def _remember_camera_index(self, camera_id: str, pose_id: str, camera_index: int) -> None:
        if not hasattr(self, "_camera_index_by_id"):
            self._camera_index_by_id = dict(CAMERA_INDEX_BY_ID)
        if not hasattr(self, "_camera_index_by_view"):
            self._camera_index_by_view = {}
        self._camera_index_by_id[camera_id] = camera_index
        self._camera_index_by_view[(camera_id, pose_id or camera_id)] = camera_index

    def _light_index(self, light_id: str) -> int:
        if light_id in LIGHT_INDEX_BY_ID:
            return LIGHT_INDEX_BY_ID[light_id]
        if light_id.startswith("LIGHT_"):
            suffix = light_id.removeprefix("LIGHT_")
            if suffix.isdigit():
                return int(suffix)
        raise ValueError(f"unknown evidence light_id for result serialization: {light_id}")

    def _release_frame_slot(self, sequence_id: int) -> None:
        slot_index = self._pending_frame_slots.pop(sequence_id, None)
        if slot_index is None:
            return
        base = self._slot_base(slot_index, self.frame_slot_size)
        AtomicU32.store(self.frames.mm, base, SlotState.EMPTY)

    def release_frame_slot(self, sequence_id: int) -> None:
        self._release_frame_slot(sequence_id)
