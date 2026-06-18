from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
BUILD_DIR = ROOT_DIR / "cpp_controller" / "build"
EXE_SUFFIX = ".exe" if os.name == "nt" else ""
CONTROLLER = BUILD_DIR / f"seat_aoi_controller{EXE_SUFFIX}"
IPC_CHECKS = BUILD_DIR / f"ipc_safety_checks{EXE_SUFFIX}"


def run(command: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, env=env, check=True)


def build_cpp() -> None:
    if shutil.which("cmake") is None:
        raise RuntimeError("缺少 cmake，Windows 工控机请先安装 CMake 与 MSVC Build Tools。")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    run(["cmake", "-S", str(ROOT_DIR / "cpp_controller"), "-B", str(BUILD_DIR)])
    run(["cmake", "--build", str(BUILD_DIR), "--config", "Release"])


def target_path(path: Path) -> Path:
    release_path = BUILD_DIR / "Release" / path.name
    if release_path.exists():
        return release_path
    return path


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
        build_cpp()
    except Exception as exc:
        print(f"构建 C++ 主控失败: {exc}", file=sys.stderr)
        return 2

    controller = target_path(CONTROLLER)
    ipc_checks = target_path(IPC_CHECKS)
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
