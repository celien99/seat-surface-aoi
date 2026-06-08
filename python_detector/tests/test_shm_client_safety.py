import mmap
import struct

from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import (
    ColorOrder,
    DTypeCode,
    FRAME_SLOT_HEADER_PREFIX,
    FRAME_SLOT_HEADER_SIZE,
    LIGHT_FRAME_META,
    PixelFormat,
    SEAT_JOB_META,
    SHM_HEADER,
    SlotState,
    AtomicU32,
    crc32,
    encode_cstr,
    frame_slot_image_offset,
    frame_slot_meta_offset,
)


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

    LIGHT_FRAME_META.pack_into(
        client.frames.mm,
        base + frame_slot_meta_offset(),
        0,
        1,
        1,
        0,
        2,
        2,
        1,
        2,
        999,
        8,
        ColorOrder.MONO,
        DTypeCode.UINT8,
        1,
        800,
        1.0,
        encode_cstr("calib/simulated_v1"),
        image_offset,
        len(image),
        crc32(image),
        0,
    )
    SEAT_JOB_META.pack_into(
        client.frames.mm,
        base + FRAME_SLOT_HEADER_PREFIX.size,
        7,
        8,
        encode_cstr("SIM"),
        encode_cstr("seat_a_black_leather"),
        encode_cstr("seat_a_black_leather_v1"),
        1,
        frame_count,
        1,
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
