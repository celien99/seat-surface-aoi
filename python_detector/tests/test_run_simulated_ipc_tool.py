from __future__ import annotations

import importlib


def test_windows_cmake_plans_prefer_visual_studio_when_nmake_is_missing(monkeypatch) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    monkeypatch.setattr(tool, "os", type("FakeOS", (), {"name": "nt", "environ": {}}))
    monkeypatch.setattr(
        tool,
        "available_cmake_generators",
        lambda: {"Visual Studio 18 2026", "NMake Makefiles", "Ninja"},
    )
    monkeypatch.setattr(tool.shutil, "which", lambda command: None)

    plans = tool.cmake_build_plans()

    assert plans[0].configure_args == ["-G", "Visual Studio 18 2026", "-A", "x64"]
    assert all("NMake Makefiles" not in plan.configure_args for plan in plans)


def test_windows_direct_compile_uses_win32_sources_and_winsock(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    monkeypatch.setattr(tool.os, "name", "nt", raising=False)
    command = tool.direct_compile_command(
        "g++",
        tool.CPP_DIR / "src" / "main.cpp",
        tmp_path / "seat_aoi_controller.exe",
    )
    command_text = "\n".join(str(part) for part in command)

    assert "shared_memory_win32.cpp" in command_text
    assert "shared_memory_posix.cpp" not in command_text
    assert "-lws2_32" in command


def test_non_windows_direct_compile_links_pthread_and_platform_shm(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    monkeypatch.setattr(tool.os, "name", "posix", raising=False)
    monkeypatch.setattr(tool.sys, "platform", "linux", raising=False)
    command = tool.direct_compile_command(
        "clang++",
        tool.CPP_DIR / "src" / "main.cpp",
        tmp_path / "seat_aoi_controller",
    )
    command_text = "\n".join(str(part) for part in command)

    assert "shared_memory_posix.cpp" in command_text
    assert "shared_memory_win32.cpp" not in command_text
    assert "-pthread" in command
    assert "-lrt" in command
