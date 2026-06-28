from python_detector.ipc.shm_protocol import ErrorCode, assert_protocol_layout, protocol_sizes


def test_protocol_sizes_match_cpp_static_asserts() -> None:
    assert_protocol_layout()
    assert protocol_sizes()["LightFrameMeta"] == 324
    assert protocol_sizes()["FrameSlotHeader"] == 268
    assert protocol_sizes()["ResultSlotHeader"] == 140
    assert protocol_sizes()["DefectResultMeta"] == 400


def test_error_code_values_match_cpp_enum() -> None:
    assert ErrorCode.LIGHT_FAULT == 10
    assert ErrorCode.CAMERA_FAULT == 11
    assert ErrorCode.TRIGGER_SYNC_FAULT == 12
    assert ErrorCode.CONFIGURATION_ERROR == 13
    assert ErrorCode.ROBOT_FAULT == 14
