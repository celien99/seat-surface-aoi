import mmap
import struct

from python_detector.ipc.data_types import DefectResult, InspectionResult
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import (
    ColorOrder,
    DEFAULT_RESULT_SLOT_SIZE,
    DEFECT_RESULT_META,
    DTypeCode,
    ErrorCode,
    FRAME_SLOT_HEADER_PREFIX,
    FRAME_SLOT_HEADER_SIZE,
    INSPECTION_RESULT_META,
    LIGHT_FRAME_META,
    PixelFormat,
    RESULT_SLOT_HEADER_PREFIX,
    RESULT_SLOT_HEADER_SIZE,
    SEAT_JOB_META,
    SHM_HEADER,
    SlotState,
    AtomicU32,
    crc32,
    encode_cstr,
    frame_slot_image_offset,
    frame_slot_meta_offset,
)


def _pack_light_frame_meta(
    mm,
    offset: int,
    *,
    camera_index: int = 0,
    pose_index: int = 0,
    light_index: int = 1,
    frame_index: int = 1,
    light_seq_index: int = 0,
    pixel_format: int = PixelFormat.MONO8,
    timestamp_us: int = 1,
    camera_id: str = "TOP_BACK",
    pose_id: str = "TOP_BACK",
    image_offset: int,
    image: bytes,
) -> None:
    LIGHT_FRAME_META.pack_into(
        mm,
        offset,
        camera_index,
        pose_index,
        light_index,
        frame_index,
        light_seq_index,
        2,
        2,
        1,
        2,
        int(pixel_format),
        8,
        ColorOrder.MONO,
        DTypeCode.UINT8,
        timestamp_us,
        10_000 + pose_index,
        timestamp_us,
        800,
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        encode_cstr(camera_id),
        encode_cstr(pose_id),
        encode_cstr("calib/simulated_v1"),
        image_offset,
        len(image),
        crc32(image),
        0,
    )


def _pack_job_meta(mm, offset: int, *, sequence_id: int, frame_count: int, view_count: int = 1) -> None:
    SEAT_JOB_META.pack_into(
        mm,
        offset,
        sequence_id,
        8,
        encode_cstr("SIM"),
        encode_cstr("seat_a_black_leather"),
        encode_cstr("seat_a_black_leather_v1"),
        view_count,
        frame_count,
        1,
        0,
        1,
    )


def _client_with_single_frame_and_result_slot(frame_slot_size: int = 1024) -> ShmClient:
    client = object.__new__(ShmClient)
    client.slot_count = 1
    client.frame_slot_size = frame_slot_size
    client.result_slot_size = DEFAULT_RESULT_SLOT_SIZE
    client.frames = type("FrameMap", (), {"mm": mmap.mmap(-1, SHM_HEADER.size + frame_slot_size)})()
    client.results = type(
        "ResultMap",
        (),
        {"mm": mmap.mmap(-1, SHM_HEADER.size + DEFAULT_RESULT_SLOT_SIZE)},
    )()
    client._pending_frame_slots = {}
    return client


def _write_single_frame_slot(
    client: ShmClient,
    *,
    slot_sequence_id: int = 7,
    job_sequence_id: int = 7,
    pixel_format: int = PixelFormat.MONO8,
    camera_id: str = "TOP_BACK",
    pose_id: str = "TOP_BACK",
    corrupt_payload_crc: bool = False,
    corrupt_header_crc: bool = False,
) -> tuple[int, int]:
    base = SHM_HEADER.size
    result_base = SHM_HEADER.size
    frame_count = 1
    image_offset = frame_slot_image_offset(frame_count)
    image = bytes([80, 81, 82, 83])
    payload_size = image_offset + len(image)
    client.frames.mm[base + image_offset : base + image_offset + len(image)] = image

    _pack_light_frame_meta(
        client.frames.mm,
        base + frame_slot_meta_offset(),
        pixel_format=int(pixel_format),
        camera_id=camera_id,
        pose_id=pose_id,
        image_offset=image_offset,
        image=image,
    )
    _pack_job_meta(
        client.frames.mm,
        base + FRAME_SLOT_HEADER_PREFIX.size,
        sequence_id=job_sequence_id,
        frame_count=frame_count,
    )
    payload_crc = crc32(memoryview(client.frames.mm)[base + frame_slot_meta_offset() : base + payload_size])
    if corrupt_payload_crc:
        payload_crc ^= 0xFFFFFFFF
    FRAME_SLOT_HEADER_PREFIX.pack_into(
        client.frames.mm,
        base,
        SlotState.READY,
        slot_sequence_id,
        payload_size,
        0,
        payload_crc,
        frame_count,
        0,
    )
    header_bytes = bytearray(client.frames.mm[base : base + FRAME_SLOT_HEADER_SIZE])
    struct.pack_into("<I", header_bytes, 0, 0)
    struct.pack_into("<I", header_bytes, 20, 0)
    header_crc = crc32(header_bytes)
    if corrupt_header_crc:
        header_crc ^= 0xFFFFFFFF
    struct.pack_into("<I", client.frames.mm, base + 20, header_crc)
    return base, result_base


def _write_invalid_image_range_slot(client: ShmClient, *, overlap: bool = False) -> tuple[int, int]:
    base = SHM_HEADER.size
    result_base = SHM_HEADER.size
    frame_count = 2 if overlap else 1
    image_offset = frame_slot_image_offset(frame_count)
    image_a = bytes([80, 80, 80, 80]) if overlap else bytes([80, 81, 82, 83])
    image_b = image_a if overlap else bytes([90, 91, 92, 93])
    payload_size = image_offset + len(image_a) + (len(image_b) if overlap else 0)
    client.frames.mm[base + image_offset : base + image_offset + len(image_a)] = image_a
    if overlap:
        client.frames.mm[base + image_offset + 2 : base + image_offset + 2 + len(image_b)] = image_b

    _pack_light_frame_meta(
        client.frames.mm,
        base + frame_slot_meta_offset(),
        image_offset=frame_slot_meta_offset() if not overlap else image_offset,
        image=image_a,
    )
    if overlap:
        _pack_light_frame_meta(
            client.frames.mm,
            base + frame_slot_meta_offset() + LIGHT_FRAME_META.size,
            light_index=2,
            light_seq_index=1,
            frame_index=2,
            image_offset=image_offset + 2,
            image=image_b,
        )
    _pack_job_meta(
        client.frames.mm,
        base + FRAME_SLOT_HEADER_PREFIX.size,
        sequence_id=7,
        frame_count=frame_count,
    )
    payload_crc = crc32(memoryview(client.frames.mm)[base + frame_slot_meta_offset() : base + payload_size])
    FRAME_SLOT_HEADER_PREFIX.pack_into(
        client.frames.mm,
        base,
        SlotState.READY,
        7,
        payload_size,
        0,
        payload_crc,
        frame_count,
        0,
    )
    header_bytes = bytearray(client.frames.mm[base : base + FRAME_SLOT_HEADER_SIZE])
    struct.pack_into("<I", header_bytes, 0, 0)
    struct.pack_into("<I", header_bytes, 20, 0)
    struct.pack_into("<I", client.frames.mm, base + 20, crc32(header_bytes))
    return base, result_base


def _write_duplicate_light_frame_slot(client: ShmClient) -> tuple[int, int]:
    base = SHM_HEADER.size
    result_base = SHM_HEADER.size
    frame_count = 2
    image_offset = frame_slot_image_offset(frame_count)
    image_a = bytes([80, 81, 82, 83])
    image_b = bytes([90, 91, 92, 93])
    images = (image_a, image_b)
    payload_size = image_offset + sum(len(image) for image in images)
    next_image_offset = image_offset
    for index, image in enumerate(images):
        client.frames.mm[base + next_image_offset : base + next_image_offset + len(image)] = image
        _pack_light_frame_meta(
            client.frames.mm,
            base + frame_slot_meta_offset() + index * LIGHT_FRAME_META.size,
            frame_index=index + 1,
            light_seq_index=index,
            timestamp_us=1_000 + index,
            image_offset=next_image_offset,
            image=image,
        )
        next_image_offset += len(image)
    _pack_job_meta(
        client.frames.mm,
        base + FRAME_SLOT_HEADER_PREFIX.size,
        sequence_id=7,
        frame_count=frame_count,
    )
    payload_crc = crc32(memoryview(client.frames.mm)[base + frame_slot_meta_offset() : base + payload_size])
    FRAME_SLOT_HEADER_PREFIX.pack_into(
        client.frames.mm,
        base,
        SlotState.READY,
        7,
        payload_size,
        0,
        payload_crc,
        frame_count,
        0,
    )
    header_bytes = bytearray(client.frames.mm[base : base + FRAME_SLOT_HEADER_SIZE])
    struct.pack_into("<I", header_bytes, 0, 0)
    struct.pack_into("<I", header_bytes, 20, 0)
    struct.pack_into("<I", client.frames.mm, base + 20, crc32(header_bytes))
    return base, result_base


def test_invalid_frame_slot_is_released_after_parse_failure() -> None:
    frame_slot_size = 1024
    client = object.__new__(ShmClient)
    client.slot_count = 1
    client.frame_slot_size = frame_slot_size
    client.frames = type("FrameMap", (), {"mm": mmap.mmap(-1, SHM_HEADER.size + frame_slot_size)})()
    client._pending_frame_slots = {}

    base = SHM_HEADER.size
    frame_count = 1
    image_offset = frame_slot_image_offset(frame_count)
    image = bytes([80, 81, 82, 83])
    payload_size = image_offset + len(image)
    client.frames.mm[base + image_offset : base + image_offset + len(image)] = image

    _pack_light_frame_meta(
        client.frames.mm,
        base + frame_slot_meta_offset(),
        pixel_format=999,
        image_offset=image_offset,
        image=image,
    )
    _pack_job_meta(
        client.frames.mm,
        base + FRAME_SLOT_HEADER_PREFIX.size,
        sequence_id=7,
        frame_count=frame_count,
    )
    payload_crc = crc32(memoryview(client.frames.mm)[base + frame_slot_meta_offset() : base + payload_size])
    FRAME_SLOT_HEADER_PREFIX.pack_into(
        client.frames.mm,
        base,
        SlotState.READY,
        7,
        payload_size,
        0,
        payload_crc,
        frame_count,
        0,
    )
    header_bytes = bytearray(client.frames.mm[base : base + FRAME_SLOT_HEADER_SIZE])
    struct.pack_into("<I", header_bytes, 0, 0)
    struct.pack_into("<I", header_bytes, 20, 0)
    struct.pack_into("<I", client.frames.mm, base + 20, crc32(header_bytes))

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, base) == SlotState.EMPTY


def test_payload_crc_failure_publishes_error_result_and_releases_input_slot() -> None:
    client = _client_with_single_frame_and_result_slot()
    frame_base, result_base = _write_single_frame_slot(client, corrupt_payload_crc=True)

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.EMPTY

    state, sequence_id, *_rest = RESULT_SLOT_HEADER_PREFIX.unpack_from(client.results.mm, result_base)
    result_meta = INSPECTION_RESULT_META.unpack_from(
        client.results.mm,
        result_base + RESULT_SLOT_HEADER_PREFIX.size,
    )
    assert state == SlotState.READY
    assert sequence_id == 7
    assert result_meta[0] == 7
    assert result_meta[1] == 8
    assert result_meta[3] == 4
    assert result_meta[5] == 0
    assert result_meta[6] == ErrorCode.CRC_MISMATCH


def test_sequence_mismatch_error_result_uses_slot_sequence_id() -> None:
    client = _client_with_single_frame_and_result_slot()
    frame_base, result_base = _write_single_frame_slot(client, slot_sequence_id=7, job_sequence_id=99)

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.EMPTY

    state, sequence_id, *_rest = RESULT_SLOT_HEADER_PREFIX.unpack_from(client.results.mm, result_base)
    result_meta = INSPECTION_RESULT_META.unpack_from(
        client.results.mm,
        result_base + RESULT_SLOT_HEADER_PREFIX.size,
    )
    assert state == SlotState.READY
    assert sequence_id == 7
    assert result_meta[0] == 7
    assert result_meta[6] == ErrorCode.INVALID_PAYLOAD


def test_robot_flyshot_camera_index_from_frame_meta_is_used_for_result_serialization() -> None:
    client = _client_with_single_frame_and_result_slot()
    frame_base, result_base = _write_single_frame_slot(
        client,
        camera_id="EYE_IN_HAND",
        pose_id="T1_BACKREST",
    )

    job = client._read_frame_slot(0)

    assert job is not None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.READING
    defect = DefectResult(
        defect_id="D1",
        class_name="scratch",
        severity="critical",
        camera_id="EYE_IN_HAND",
        pose_id="T1_BACKREST",
        roi_name="full",
        bbox_xyxy_pixel=(0, 0, 1, 1),
        score=0.9,
        area_px=4,
        evidence_lights=["DIFFUSE"],
        mask_offset=None,
        decision="NG",
    )
    client._write_result_slot(
        result_base,
        InspectionResult(
            sequence_id=7,
            trigger_id=8,
            seat_id="SIM",
            decision="NG",
            defects=[defect],
            quality_pass=True,
            error_code=0,
            elapsed_ms=1.0,
        ),
        [defect],
        RESULT_SLOT_HEADER_SIZE + DEFECT_RESULT_META.size,
    )

    unpacked = DEFECT_RESULT_META.unpack_from(client.results.mm, result_base + RESULT_SLOT_HEADER_SIZE)
    assert unpacked[3] == 0
    assert unpacked[4].split(b"\0", 1)[0].decode() == "EYE_IN_HAND"
    assert unpacked[5].split(b"\0", 1)[0].decode() == "T1_BACKREST"


def test_duplicate_camera_light_frame_publishes_invalid_payload_error() -> None:
    client = _client_with_single_frame_and_result_slot(frame_slot_size=2048)
    frame_base, result_base = _write_duplicate_light_frame_slot(client)

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.EMPTY

    state, sequence_id, *_rest = RESULT_SLOT_HEADER_PREFIX.unpack_from(client.results.mm, result_base)
    result_meta = INSPECTION_RESULT_META.unpack_from(
        client.results.mm,
        result_base + RESULT_SLOT_HEADER_PREFIX.size,
    )
    assert state == SlotState.READY
    assert sequence_id == 7
    assert result_meta[0] == 7
    assert result_meta[6] == ErrorCode.INVALID_PAYLOAD


def test_header_crc_failure_releases_slot_without_untrusted_result() -> None:
    client = _client_with_single_frame_and_result_slot()
    frame_base, result_base = _write_single_frame_slot(client, corrupt_header_crc=True)

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.EMPTY
    assert AtomicU32.load(client.results.mm, result_base) == SlotState.EMPTY


def test_image_offset_pointing_to_meta_publishes_invalid_payload_error() -> None:
    client = _client_with_single_frame_and_result_slot(frame_slot_size=2048)
    frame_base, result_base = _write_invalid_image_range_slot(client)

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.EMPTY

    state, sequence_id, *_rest = RESULT_SLOT_HEADER_PREFIX.unpack_from(client.results.mm, result_base)
    result_meta = INSPECTION_RESULT_META.unpack_from(
        client.results.mm,
        result_base + RESULT_SLOT_HEADER_PREFIX.size,
    )
    assert state == SlotState.READY
    assert sequence_id == 7
    assert result_meta[6] == ErrorCode.INVALID_PAYLOAD


def test_overlapping_image_ranges_publish_invalid_payload_error() -> None:
    client = _client_with_single_frame_and_result_slot(frame_slot_size=2048)
    frame_base, result_base = _write_invalid_image_range_slot(client, overlap=True)

    assert client._read_frame_slot(0) is None
    assert AtomicU32.load(client.frames.mm, frame_base) == SlotState.EMPTY

    state, sequence_id, *_rest = RESULT_SLOT_HEADER_PREFIX.unpack_from(client.results.mm, result_base)
    result_meta = INSPECTION_RESULT_META.unpack_from(
        client.results.mm,
        result_base + RESULT_SLOT_HEADER_PREFIX.size,
    )
    assert state == SlotState.READY
    assert sequence_id == 7
    assert result_meta[6] == ErrorCode.INVALID_PAYLOAD
