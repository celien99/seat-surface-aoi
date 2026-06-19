from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
CPP_DIR = ROOT_DIR / "cpp_controller"
BUILD_ROOT = CPP_DIR / "build" / "simulated-ipc"
EXE_SUFFIX = ".exe" if os.name == "nt" else ""


@dataclass(frozen=True)
class BuildArtifacts:
    controller: Path
    ipc_checks: Path


@dataclass(frozen=True)
class CMakeBuildPlan:
    name: str
    configure_args: list[str]


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def command_output(command: list[str]) -> str:
    completed = subprocess.run(
        command,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def slug(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "default"


def available_cmake_generators() -> set[str]:
    if shutil.which("cmake") is None:
        return set()
    generators: set[str] = set()
    for raw_line in command_output(["cmake", "--help"]).splitlines():
        line = raw_line.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        if "=" not in line:
            continue
        generators.add(line.split("=", 1)[0].strip())
    return generators


def cmake_build_plans() -> list[CMakeBuildPlan]:
    generators = available_cmake_generators()
    if not generators:
        return []

    plans: list[CMakeBuildPlan] = []
    seen: set[str] = set()

    def add_plan(generator: str, *extra_args: str) -> None:
        if generator not in generators or generator in seen:
            return
        seen.add(generator)
        plans.append(
            CMakeBuildPlan(
                name=f"cmake-{slug(generator)}",
                configure_args=["-G", generator, *extra_args],
            )
        )

    env_generator = os.environ.get("CMAKE_GENERATOR")
    if env_generator:
        env_args: list[str] = []
        env_platform = os.environ.get("CMAKE_GENERATOR_PLATFORM")
        if env_platform:
            env_args.extend(["-A", env_platform])
        add_plan(env_generator, *env_args)

    if os.name == "nt":
        if shutil.which("ninja") is not None:
            add_plan("Ninja")
        for generator in (
            "Visual Studio 18 2026",
            "Visual Studio 17 2022",
            "Visual Studio 16 2019",
            "Visual Studio 15 2017",
        ):
            add_plan(generator, "-A", "x64")
        if shutil.which("nmake") is not None:
            add_plan("NMake Makefiles")
        if shutil.which("mingw32-make") is not None:
            add_plan("MinGW Makefiles")
    else:
        if shutil.which("ninja") is not None:
            add_plan("Ninja")
        add_plan("Unix Makefiles")

    return plans


def target_path(build_dir: Path, name: str) -> Path:
    release_path = build_dir / "Release" / name
    if release_path.exists():
        return release_path
    return build_dir / name


def is_multi_config_generator(generator: str) -> bool:
    return generator.startswith("Visual Studio ") or generator == "Ninja Multi-Config"


def cmake_configure_command(plan: CMakeBuildPlan, build_dir: Path) -> list[str]:
    command = [
        "cmake",
        "-S",
        str(CPP_DIR),
        "-B",
        str(build_dir),
        *plan.configure_args,
    ]
    if not is_multi_config_generator(plan.configure_args[1]):
        command.append("-DCMAKE_BUILD_TYPE=Release")
    return command


def build_cpp_with_cmake(plan: CMakeBuildPlan) -> BuildArtifacts:
    build_dir = BUILD_ROOT / plan.name
    build_dir.mkdir(parents=True, exist_ok=True)
    run(cmake_configure_command(plan, build_dir))
    run(["cmake", "--build", str(build_dir), "--config", "Release"])
    return BuildArtifacts(
        controller=target_path(build_dir, f"seat_aoi_controller{EXE_SUFFIX}"),
        ipc_checks=target_path(build_dir, f"ipc_safety_checks{EXE_SUFFIX}"),
    )


def shared_memory_source() -> Path:
    if os.name == "nt":
        return CPP_DIR / "src" / "ipc" / "shared_memory_win32.cpp"
    return CPP_DIR / "src" / "ipc" / "shared_memory_posix.cpp"


def common_cpp_sources() -> list[Path]:
    return [
        CPP_DIR / "src" / "ipc" / "crc32.cpp",
        shared_memory_source(),
        CPP_DIR / "src" / "ipc" / "frame_ring_buffer.cpp",
        CPP_DIR / "src" / "ipc" / "result_ring_buffer.cpp",
        CPP_DIR / "src" / "control" / "hardware_backend.cpp",
        CPP_DIR / "src" / "control" / "fl_acdh_light_controller.cpp",
        CPP_DIR / "src" / "control" / "light_controller.cpp",
        CPP_DIR / "src" / "control" / "signal_client.cpp",
        CPP_DIR / "src" / "control" / "tcp_signal_client.cpp",
        CPP_DIR / "src" / "control" / "robot_client.cpp",
        CPP_DIR / "src" / "control" / "production_event_log.cpp",
        CPP_DIR / "src" / "control" / "station_health.cpp",
        CPP_DIR / "src" / "control" / "station_runtime_config.cpp",
        CPP_DIR / "src" / "camera" / "camera_device.cpp",
        CPP_DIR / "src" / "camera" / "hikrobot_mvs_camera.cpp",
        CPP_DIR / "src" / "camera" / "camera_worker.cpp",
        CPP_DIR / "src" / "control" / "trigger_scheduler.cpp",
        CPP_DIR / "src" / "control" / "frame_assembler.cpp",
        CPP_DIR / "src" / "control" / "station_controller.cpp",
        CPP_DIR / "src" / "control" / "image_writer.cpp",
        CPP_DIR / "src" / "control" / "distance_sensor.cpp",
        CPP_DIR / "src" / "control" / "distance_trigger_signal_client.cpp",
    ]


def direct_compile_command(compiler: str, entry: Path, output: Path) -> list[str]:
    command = [
        compiler,
        "-std=c++17",
        "-O2",
        "-I",
        str(CPP_DIR / "include"),
    ]
    if os.name == "nt":
        command.extend(["-DNOMINMAX", "-DWIN32_LEAN_AND_MEAN"])
    command.extend(str(path) for path in [entry, *common_cpp_sources()])
    command.extend(["-o", str(output)])
    if os.name == "nt":
        command.append("-lws2_32")
    elif sys.platform.startswith("linux"):
        command.extend(["-pthread", "-lrt"])
    else:
        command.append("-pthread")
    return command


def direct_compiler_candidates() -> list[str]:
    candidates = [
        os.environ.get("CXX", ""),
        "clang++",
        "g++",
        "c++",
    ]
    result: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        resolved = shutil.which(candidate)
        if resolved is not None and resolved not in result:
            result.append(resolved)
    return result


def build_cpp_direct(compiler: str) -> BuildArtifacts:
    build_dir = BUILD_ROOT / f"direct-{slug(Path(compiler).stem)}"
    build_dir.mkdir(parents=True, exist_ok=True)
    controller = build_dir / f"seat_aoi_controller{EXE_SUFFIX}"
    ipc_checks = build_dir / f"ipc_safety_checks{EXE_SUFFIX}"
    run(direct_compile_command(compiler, CPP_DIR / "src" / "main.cpp", controller))
    run(direct_compile_command(compiler, CPP_DIR / "tools" / "ipc_safety_checks.cpp", ipc_checks))
    return BuildArtifacts(controller=controller, ipc_checks=ipc_checks)


def build_cpp() -> BuildArtifacts:
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []

    for plan in cmake_build_plans():
        try:
            print(f"使用 CMake 生成器构建 C++ 主控: {plan.configure_args[1]}", flush=True)
            return build_cpp_with_cmake(plan)
        except subprocess.CalledProcessError as exc:
            errors.append(f"{plan.configure_args[1]}: {exc}")
            print(f"CMake 生成器 {plan.configure_args[1]} 构建失败，尝试下一个构建方式。", file=sys.stderr)

    for compiler in direct_compiler_candidates():
        try:
            print(f"使用直接编译器构建 C++ 主控: {compiler}", flush=True)
            return build_cpp_direct(compiler)
        except subprocess.CalledProcessError as exc:
            errors.append(f"{compiler}: {exc}")
            print(f"直接编译器 {compiler} 构建失败，尝试下一个构建方式。", file=sys.stderr)

    hint = (
        "无法构建 C++ 主控。请安装 Visual Studio Build Tools 的 C++ 工作负载，"
        "或安装 Ninja/clang++/g++；也可以进入 x64 VS 开发命令环境后重试。"
    )
    if errors:
        hint += "\n已尝试的构建方式:\n  " + "\n  ".join(errors[-6:])
    elif shutil.which("cmake") is None:
        hint += "\n当前 PATH 中未找到 cmake。"
    raise RuntimeError(hint)


def python_runner() -> list[str]:
    if shutil.which("uv") is not None:
        return ["uv", "run", "python"]
    return [sys.executable]


def main() -> int:
    parser = argparse.ArgumentParser(description="运行跨平台端到端模拟 IPC")
    parser.add_argument(
        "--config",
        default=str(ROOT_DIR / "cpp_controller" / "config" / "station_runtime.example.conf"),
        help="C++ station runtime config 路径",
    )
    args = parser.parse_args()

    try:
        artifacts = build_cpp()
    except Exception as exc:
        print(f"构建 C++ 主控失败: {exc}", file=sys.stderr)
        return 2

    controller = artifacts.controller
    ipc_checks = artifacts.ipc_checks
    if not controller.exists() or not ipc_checks.exists():
        print("缺少 C++ 构建产物", file=sys.stderr)
        return 2

    run([str(ipc_checks)])
    subprocess.run([str(controller), "--cleanup"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    controller_args = [str(controller), "--config", args.config, "--once", "--wait-ms", "8000"]
    detector_args = [
        "-m",
        "python_detector.detector_main",
        "--config",
        args.config,
        "--once",
        "--timeout-ms",
        "8000",
    ]

    cpp_process = subprocess.Popen(controller_args)
    time.sleep(0.2)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    try:
        run(
            python_runner() + detector_args,
            cwd=ROOT_DIR,
            env=env,
        )
        cpp_status = cpp_process.wait()
    finally:
        if cpp_process.poll() is None:
            cpp_process.terminate()
            cpp_process.wait(timeout=5)
        subprocess.run([str(controller), "--cleanup"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return cpp_status


if __name__ == "__main__":
    raise SystemExit(main())
