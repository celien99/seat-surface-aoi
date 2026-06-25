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


def test_stale_cmake_cache_detects_relocated_source_and_build(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    current_source = tmp_path / "current" / "cpp_controller"
    current_build = current_source / "build" / "simulated-ipc" / "cmake-visual-studio-17-2022"
    current_build.mkdir(parents=True)
    (current_build / "CMakeCache.txt").write_text(
        "\n".join(
            [
                "CMAKE_HOME_DIRECTORY:INTERNAL=E:/code/seat-surface-aoi/cpp_controller",
                "CMAKE_CACHEFILE_DIR:INTERNAL=E:/code/seat-surface-aoi/cpp_controller/build/simulated-ipc/cmake-visual-studio-17-2022",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(tool, "CPP_DIR", current_source)

    reasons = tool.stale_cmake_cache_reasons(current_build)

    assert reasons == [
        "source=E:/code/seat-surface-aoi/cpp_controller",
        "build=E:/code/seat-surface-aoi/cpp_controller/build/simulated-ipc/cmake-visual-studio-17-2022",
    ]


def test_prepare_cmake_build_dir_recreates_only_stale_plan_dir(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    build_root = tmp_path / "cpp_controller" / "build" / "simulated-ipc"
    stale_build = build_root / "cmake-visual-studio-17-2022"
    sibling_build = build_root / "cmake-ninja"
    stale_build.mkdir(parents=True)
    sibling_build.mkdir(parents=True)
    (stale_build / "stale.obj").write_text("old artifact", encoding="utf-8")
    (stale_build / "CMakeCache.txt").write_text(
        "\n".join(
            [
                "CMAKE_HOME_DIRECTORY:INTERNAL=C:/old/seat-surface-aoi/cpp_controller",
                "CMAKE_CACHEFILE_DIR:INTERNAL=C:/old/seat-surface-aoi/cpp_controller/build/simulated-ipc/cmake-visual-studio-17-2022",
            ]
        ),
        encoding="utf-8",
    )
    (sibling_build / "artifact.obj").write_text("keep", encoding="utf-8")

    monkeypatch.setattr(tool, "CPP_DIR", tmp_path / "cpp_controller")
    monkeypatch.setattr(tool, "BUILD_ROOT", build_root)

    tool.prepare_cmake_build_dir(stale_build)

    assert stale_build.exists()
    assert not (stale_build / "stale.obj").exists()
    assert (sibling_build / "artifact.obj").exists()


def test_prepare_cmake_build_dir_keeps_matching_cache(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    cpp_dir = tmp_path / "cpp_controller"
    build_root = cpp_dir / "build" / "simulated-ipc"
    build_dir = build_root / "cmake-ninja"
    build_dir.mkdir(parents=True)
    (build_dir / "artifact.obj").write_text("keep", encoding="utf-8")
    (build_dir / "CMakeCache.txt").write_text(
        "\n".join(
            [
                f"CMAKE_HOME_DIRECTORY:INTERNAL={cpp_dir}",
                f"CMAKE_CACHEFILE_DIR:INTERNAL={build_dir}",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(tool, "CPP_DIR", cpp_dir)
    monkeypatch.setattr(tool, "BUILD_ROOT", build_root)

    tool.prepare_cmake_build_dir(build_dir)

    assert (build_dir / "artifact.obj").read_text(encoding="utf-8") == "keep"


def test_replay_capture_flag_passes_replay_config_to_controller_and_detector(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    controller = tmp_path / "seat_aoi_controller.exe"
    protocol_layout = tmp_path / "protocol_layout.exe"
    ipc_checks = tmp_path / "ipc_safety_checks.exe"
    for path in (controller, protocol_layout, ipc_checks):
        path.write_text("", encoding="utf-8")
    artifacts = tool.BuildArtifacts(
        controller=controller,
        protocol_layout=protocol_layout,
        ipc_checks=ipc_checks,
    )
    popen_commands: list[list[str]] = []
    run_commands: list[list[str]] = []

    class FakeProcess:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

    def fake_run(command, *, cwd=None, env=None):
        run_commands.append([str(item) for item in command])

    def fake_popen(command):
        popen_commands.append([str(item) for item in command])
        return FakeProcess()

    monkeypatch.setattr(tool, "build_cpp", lambda: artifacts)
    monkeypatch.setattr(tool, "run", fake_run)
    monkeypatch.setattr(tool, "python_runner", lambda: ["python"])
    monkeypatch.setattr(tool.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(tool.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool.time, "sleep", lambda _seconds: None)

    assert tool.main(["--replay-capture"]) == 0

    replay_config = str(tool.REPLAY_CAPTURE_CONFIG)
    assert popen_commands
    assert ["--config", replay_config] == popen_commands[0][1:3]
    assert "--wait-ms" not in popen_commands[0]
    detector_command = run_commands[1]
    assert detector_command[:3] == ["python", "-m", "python_detector.detector_main"]
    assert ["--config", replay_config] == detector_command[3:5]
    assert detector_command[-1] == "120000"


def test_timeout_override_is_passed_to_controller_and_detector(monkeypatch, tmp_path) -> None:
    tool = importlib.import_module("tools.run_simulated_ipc")

    controller = tmp_path / "seat_aoi_controller.exe"
    protocol_layout = tmp_path / "protocol_layout.exe"
    ipc_checks = tmp_path / "ipc_safety_checks.exe"
    for path in (controller, protocol_layout, ipc_checks):
        path.write_text("", encoding="utf-8")
    artifacts = tool.BuildArtifacts(
        controller=controller,
        protocol_layout=protocol_layout,
        ipc_checks=ipc_checks,
    )
    popen_commands: list[list[str]] = []
    run_commands: list[list[str]] = []

    class FakeProcess:
        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            return None

    monkeypatch.setattr(tool, "build_cpp", lambda: artifacts)
    monkeypatch.setattr(tool, "run", lambda command, *, cwd=None, env=None: run_commands.append([str(item) for item in command]))
    monkeypatch.setattr(tool, "python_runner", lambda: ["python"])
    monkeypatch.setattr(tool.subprocess, "Popen", lambda command: popen_commands.append([str(item) for item in command]) or FakeProcess())
    monkeypatch.setattr(tool.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(tool.time, "sleep", lambda _seconds: None)

    assert tool.main(["--replay-capture", "--timeout-ms", "9000"]) == 0

    assert ["--wait-ms", "9000"] == popen_commands[0][-2:]
    assert run_commands[1][-1] == "9000"
