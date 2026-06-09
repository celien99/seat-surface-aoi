import mmap

import pytest

from python_detector.ipc.data_types import DefectResult, InspectionResult
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import (
    DEFAULT_RESULT_SLOT_SIZE,
    DEFECT_RESULT_META,
    RESULT_SLOT_HEADER_PREFIX,
    RESULT_SLOT_HEADER_SIZE,
    SlotState,
    result_slot_defects_offset,
)


def test_write_result_slot_serializes_ok_result() -> None:
    client = object.__new__(ShmClient)
    client.result_slot_size = DEFAULT_RESULT_SLOT_SIZE
    client.results = type("ResultMap", (), {"mm": mmap.mmap(-1, DEFAULT_RESULT_SLOT_SIZE)})()
    result = InspectionResult(
        sequence_id=7,
        trigger_id=8,
        seat_id="SIM",
        decision="OK",
        defects=[],
        quality_pass=True,
        error_code=0,
        elapsed_ms=1.5,
    )
    client._write_result_slot(0, result, [], 140)
    state, sequence_id, *_rest = RESULT_SLOT_HEADER_PREFIX.unpack_from(client.results.mm, 0)
    assert state == SlotState.WRITING
    assert sequence_id == 7


def test_write_result_slot_preserves_camera_and_evidence_indices() -> None:
    client = object.__new__(ShmClient)
    client.result_slot_size = DEFAULT_RESULT_SLOT_SIZE
    client.results = type("ResultMap", (), {"mm": mmap.mmap(-1, DEFAULT_RESULT_SLOT_SIZE)})()
    defect = DefectResult(
        defect_id="D1",
        class_name="scratch",
        severity="critical",
        camera_id="TOP_CUSHION",
        roi_name="full",
        bbox_xyxy_pixel=(2, 3, 9, 10),
        score=0.9,
        area_px=64,
        evidence_lights=["HIGH_LEFT", "HIGH_RIGHT"],
        mask_offset=None,
        decision="NG",
    )
    result = InspectionResult(
        sequence_id=7,
        trigger_id=8,
        seat_id="SIM",
        decision="NG",
        defects=[defect],
        quality_pass=True,
        error_code=0,
        elapsed_ms=1.5,
    )

    client._write_result_slot(0, result, [defect], RESULT_SLOT_HEADER_SIZE + DEFECT_RESULT_META.size)

    unpacked = DEFECT_RESULT_META.unpack_from(client.results.mm, result_slot_defects_offset())
    camera_index = unpacked[3]
    evidence_light_count = unpacked[11]
    evidence_lights = unpacked[12:20]
    assert camera_index == 1
    assert evidence_light_count == 2
    assert evidence_lights[:2] == (3, 4)


def test_pack_defect_rejects_unknown_camera_id() -> None:
    client = object.__new__(ShmClient)
    defect = _defect(camera_id="SIDE_TOP")

    with pytest.raises(ValueError, match="unknown camera_id"):
        client._pack_defect(defect)


def test_pack_defect_rejects_unknown_evidence_light() -> None:
    client = object.__new__(ShmClient)
    defect = _defect(evidence_lights=["HIGH_LEFT", "UNKNOWN_LIGHT"])

    with pytest.raises(ValueError, match="unknown evidence light_id"):
        client._pack_defect(defect)


def test_pack_defect_rejects_too_many_evidence_lights() -> None:
    client = object.__new__(ShmClient)
    defect = _defect(evidence_lights=[f"LIGHT_{index}" for index in range(1, 10)])

    with pytest.raises(ValueError, match="too many evidence_lights"):
        client._pack_defect(defect)


def _defect(
    camera_id: str = "TOP_BACK",
    evidence_lights: list[str] | None = None,
) -> DefectResult:
    return DefectResult(
        defect_id="D1",
        class_name="scratch",
        severity="critical",
        camera_id=camera_id,
        roi_name="full",
        bbox_xyxy_pixel=(2, 3, 9, 10),
        score=0.9,
        area_px=64,
        evidence_lights=evidence_lights or ["HIGH_LEFT", "HIGH_RIGHT"],
        mask_offset=None,
        decision="NG",
    )
