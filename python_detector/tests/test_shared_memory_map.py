from python_detector.ipc.shared_memory_map import platform_shared_memory_name


def test_windows_shared_memory_name_uses_local_namespace(monkeypatch):
    monkeypatch.setattr("sys.platform", "win32")

    assert platform_shared_memory_name("/seat_aoi_cpp_to_py_frames_v1") == "Local\\seat_aoi_cpp_to_py_frames_v1"
    assert platform_shared_memory_name("Global\\seat_aoi_cpp_to_py_frames_v1") == "Global\\seat_aoi_cpp_to_py_frames_v1"


def test_posix_shared_memory_name_is_unchanged(monkeypatch):
    monkeypatch.setattr("sys.platform", "linux")

    assert platform_shared_memory_name("/seat_aoi_cpp_to_py_frames_v1") == "/seat_aoi_cpp_to_py_frames_v1"
