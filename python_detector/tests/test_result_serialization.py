import mmap

from python_detector.ipc.data_types import InspectionResult
from python_detector.ipc.shm_client import ShmClient
from python_detector.ipc.shm_protocol import DEFAULT_RESULT_SLOT_SIZE, RESULT_SLOT_HEADER_PREFIX, SlotState


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

