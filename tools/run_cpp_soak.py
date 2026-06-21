from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tools.run_simulated_ipc import ROOT_DIR, build_cpp, python_runner, run


DEFAULT_TRACE_ROOT = ROOT_DIR / "trace" / "cpp_soak"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (ROOT_DIR / path).resolve()


def _is_under_workspace(path: Path) -> bool:
    try:
        path.relative_to(ROOT_DIR)
    except ValueError:
        return False
    return True


def _tail_text(path: Path, max_lines: int = 20) -> str:
    if not path.exists():
        return ""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-max_lines:])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 C++/Python 共享内存短时稳压测试")
    parser.add_argument("--jobs", type=int, default=20, help="循环次数，默认 20")
    parser.add_argument("--wait-ms", type=int, default=8000, help="每轮等待 detector 的超时时间，默认 8000")
    parser.add_argument(
        "--trace-root",
        default=str(DEFAULT_TRACE_ROOT),
        help="C++ 事件日志输出目录，默认 trace/cpp_soak",
    )
    parser.add_argument(
        "--config",
        default="",
        help="C++ station runtime config 路径；默认使用 C++ 内置 simulated fallback",
    )
    args = parser.parse_args(argv)

    if args.jobs <= 0:
        print("--jobs 必须大于 0", file=sys.stderr)
        return 2
    if args.wait_ms <= 0:
        print("--wait-ms 必须大于 0", file=sys.stderr)
        return 2

    try:
        artifacts = build_cpp()
    except Exception as exc:
        print(f"构建 C++ 主控失败: {exc}", file=sys.stderr)
        return 2

    controller = artifacts.controller
    if not controller.exists():
        print(f"缺少 C++ 主控构建产物: {controller}", file=sys.stderr)
        return 2

    trace_root = _resolve_path(args.trace_root)
    if trace_root == ROOT_DIR or not _is_under_workspace(trace_root):
        print(f"--trace-root 必须位于仓库目录内，且不能是仓库根目录: {trace_root}", file=sys.stderr)
        return 2
    if trace_root.exists():
        shutil.rmtree(trace_root)
    trace_root.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(controller), "--cleanup"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    ok_count = 0
    failed_count = 0
    start = time.monotonic()

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    try:
        for index in range(1, args.jobs + 1):
            controller_args = [
                str(controller),
                "--once",
                "--wait-ms",
                str(args.wait_ms),
                "--trace-root",
                str(trace_root),
            ]
            if args.config:
                controller_args[1:1] = ["--config", args.config]
            cpp_process = subprocess.Popen(controller_args)
            time.sleep(0.2)
            detector_status = 0
            try:
                detector_args = [
                    "-m",
                    "python_detector.detector_main",
                    "--once",
                    "--timeout-ms",
                    str(args.wait_ms),
                ]
                if args.config:
                    detector_args[2:2] = ["--config", args.config]
                run(
                    python_runner() + detector_args,
                    cwd=ROOT_DIR,
                    env=env,
                )
            except subprocess.CalledProcessError:
                detector_status = 1
                print(f"detector 第 {index} 轮失败", file=sys.stderr)

            cpp_status = cpp_process.wait()
            if cpp_status == 0 and detector_status == 0:
                ok_count += 1
            else:
                failed_count += 1
    finally:
        subprocess.run([str(controller), "--cleanup"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    elapsed_s = int(time.monotonic() - start)
    event_log = trace_root / "cpp_controller_events.jsonl"
    summary = trace_root / "summary.txt"
    lines = [
        f"jobs={args.jobs}",
        f"ok_iterations={ok_count}",
        f"failed_iterations={failed_count}",
        f"elapsed_s={elapsed_s}",
    ]
    if event_log.exists():
        lines.append(f"event_log={event_log}")
        recent_events = _tail_text(event_log)
        if recent_events:
            lines.append("recent_events:")
            lines.append(recent_events)
    summary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(summary.read_text(encoding="utf-8"), end="")

    return 1 if failed_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
