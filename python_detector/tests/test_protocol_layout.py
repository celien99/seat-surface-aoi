from python_detector.ipc.shm_protocol import assert_protocol_layout, protocol_sizes


def test_protocol_sizes_match_cpp_static_asserts() -> None:
    assert_protocol_layout()
    assert protocol_sizes()["LightFrameMeta"] == 152
    assert protocol_sizes()["FrameSlotHeader"] == 260
    assert protocol_sizes()["ResultSlotHeader"] == 140
    assert protocol_sizes()["DefectResultMeta"] == 336
