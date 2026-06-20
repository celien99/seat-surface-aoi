from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from tools.run_simulated_ipc import EXE_SUFFIX, ROOT_DIR, BuildArtifacts, build_cpp, run


DEFAULT_OUTPUT_DIR = ROOT_DIR / "dist"
MODEL_DIR = ROOT_DIR / "model"


def _resolve_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return ROOT_DIR / path


def _python_runner() -> list[str]:
    if shutil.which("uv") is not None:
        return ["uv", "run", "python"]
    return [sys.executable]


def _pytest_runner() -> list[str]:
    if shutil.which("uv") is not None:
        return ["uv", "run", "pytest"]
    return [sys.executable, "-m", "pytest"]


def _git_short() -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT_DIR), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "nogit"
    return completed.stdout.strip() or "nogit"


def _git_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(ROOT_DIR), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return completed.stdout.strip() or "unknown"


def _git_dirty() -> bool:
    try:
        diff = subprocess.run(["git", "-C", str(ROOT_DIR), "diff", "--quiet", "--ignore-submodules", "--"])
        cached = subprocess.run(
            ["git", "-C", str(ROOT_DIR), "diff", "--cached", "--quiet", "--ignore-submodules", "--"]
        )
    except FileNotFoundError:
        return False
    return diff.returncode != 0 or cached.returncode != 0


def _copy_tree(source_dir: Path, target_dir: Path) -> None:
    ignore = shutil.ignore_patterns(
        "__pycache__",
        "*.pyc",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".DS_Store",
        "build",
        "dist",
    )
    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(source_dir, target_dir, ignore=ignore)


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _write_package_readme(stage_dir: Path) -> None:
    text = """# Seat Surface AOI 离线部署包

本包用于部署或联调汽车座椅表面 AOI 参考链路。在线主链路仍保持 C++ 实时主控和 Python 独立检测进程分工：C++ 负责 PLC、相机、频闪、机器人、共享内存写入和结果读取；Python 只负责质量门禁、预处理、模型推理、融合和规则判定。

## 包内容

```text
bin/                 # 已构建 C++ 可执行文件
cpp_controller/      # C++ 主控源码、配置、CMake 工程和工具源码
python_detector/     # Python 在线检测进程、配方、标定、算法和测试
display_app/         # PySide6/QML 展示前端，只读 detector display 通道
training_tools/      # 离线回放、benchmark、训练样本和模型资产工具
model/               # 模型目录结构或真实部署模型资产
tools/               # 协议、模型资产、架构检查和模拟 IPC 脚本
docs/                # 架构、共享内存协议和运维文档
```

## 快速校验

```powershell
uv run python validate_package.py
uv run python run_packaged_simulated_ipc.py
```

上 Windows 工控机或产线联调前，先运行部署预检：

```powershell
$env:PYTHONPATH="."
uv run python -m tools.validate_deployment_preflight
uv run python -m tools.validate_deployment_preflight --strict-production
```

默认预检用于交接，会把真实模型和 MES/监控接口列为现场 ACTION；`--strict-production` 用于上机放行前，会把真实模型、固定双机位正式生产配置缺失和光源/配方不一致作为阻塞项。

## 生产模型

生产包必须先把真实模型产物放入 `model/`，打包脚本会默认集成该目录。占位模型只能用于参考链路和联调包，不能作为生产包放行。

## 启动入口

```powershell
# C++ 主控，解包后可直接使用 bin/ 内已构建产物
.\bin\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.example.conf --once --wait-ms 8000

# Python detector
$env:PYTHONPATH="."
uv run python -m python_detector.detector_main --once --timeout-ms 8000

# PySide6/QML 展示前端，需要安装 display extra
uv sync --extra display
uv run seat-aoi-display --trace-root trace --line-id AOI-1
```

在线图像和检测结果只能通过共享内存交换，不使用 TCP；Windows 工控机使用 Named Shared Memory。任何缺帧、超时、协议错误、CRC 错误、质量门禁失败或模型异常都不能输出 OK。
"""
    (stage_dir / "PACKAGE_README.md").write_text(text, encoding="utf-8", newline="\n")


def _write_validate_package(stage_dir: Path) -> None:
    text = r'''from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
EXE_SUFFIX = ".exe" if os.name == "nt" else ""


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT_DIR, env=env, check=True)


def main() -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    python = [sys.executable]
    run(python + ["-m", "tools.validate_protocol"], env=env)
    run(python + ["-m", "tools.validate_deployment_preflight"], env=env)
    run([str(ROOT_DIR / "bin" / f"protocol_layout{EXE_SUFFIX}")])
    run([str(ROOT_DIR / "bin" / f"ipc_safety_checks{EXE_SUFFIX}")])
    print("部署包基础校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    (stage_dir / "validate_package.py").write_text(text, encoding="utf-8", newline="\n")


def _write_packaged_ipc(stage_dir: Path) -> None:
    text = r'''from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent
EXE_SUFFIX = ".exe" if os.name == "nt" else ""


def python_runner() -> list[str]:
    if shutil.which("uv") is not None:
        return ["uv", "run", "python"]
    return [sys.executable]


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    subprocess.run(command, cwd=ROOT_DIR, env=env, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行部署包内 C++ 产物的端到端模拟 IPC")
    parser.add_argument(
        "--config",
        default=str(ROOT_DIR / "cpp_controller" / "config" / "station_runtime.example.conf"),
        help="C++ station runtime config 路径",
    )
    args = parser.parse_args()

    controller = ROOT_DIR / "bin" / f"seat_aoi_controller{EXE_SUFFIX}"
    ipc_checks = ROOT_DIR / "bin" / f"ipc_safety_checks{EXE_SUFFIX}"
    if not controller.exists():
        print(f"缺少 C++ 主控: {controller}", file=sys.stderr)
        return 2
    if not ipc_checks.exists():
        print(f"缺少 IPC 诊断工具: {ipc_checks}", file=sys.stderr)
        return 2

    run([str(ipc_checks)])
    subprocess.run([str(controller), "--cleanup"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cpp_process = subprocess.Popen([str(controller), "--config", args.config, "--once", "--wait-ms", "8000"])
    time.sleep(0.2)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    try:
        run(
            python_runner()
            + [
                "-m",
                "python_detector.detector_main",
                "--config",
                args.config,
                "--once",
                "--timeout-ms",
                "8000",
            ],
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
'''
    (stage_dir / "run_packaged_simulated_ipc.py").write_text(text, encoding="utf-8", newline="\n")


def _write_manifest(stage_dir: Path, package_name: str, created_at: str, model_dir: Path) -> None:
    manifest = {
        "package_name": package_name,
        "created_at_utc": created_at,
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "platform": platform.platform(),
        "model_dir": str(model_dir),
        "components": [
            f"bin/seat_aoi_controller{EXE_SUFFIX}",
            f"bin/protocol_layout{EXE_SUFFIX}",
            f"bin/ipc_safety_checks{EXE_SUFFIX}",
            "cpp_controller",
            "python_detector",
            "display_app",
            "training_tools",
            "model",
            "tools",
            "docs",
        ],
    }
    (stage_dir / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_file_list(stage_dir: Path) -> None:
    files = sorted(path.relative_to(stage_dir).as_posix() for path in stage_dir.rglob("*") if path.is_file())
    (stage_dir / "PACKAGE_FILES.txt").write_text("\n".join(files) + "\n", encoding="utf-8")


def _write_sha256(path: Path) -> None:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest.hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )


def _make_archive(stage_dir: Path, archive_path: Path) -> None:
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(stage_dir, arcname=stage_dir.name)


def _validate_artifacts(artifacts: BuildArtifacts) -> None:
    missing = [
        path
        for path in (artifacts.controller, artifacts.protocol_layout, artifacts.ipc_checks)
        if not path.exists()
    ]
    if missing:
        raise RuntimeError(f"缺少 C++ 构建产物: {missing}")


def _default_existing_artifacts() -> BuildArtifacts:
    build_root = ROOT_DIR / "cpp_controller" / "build"
    candidates = sorted(build_root.rglob(f"seat_aoi_controller{EXE_SUFFIX}"))
    for controller in candidates:
        base = controller.parent
        artifacts = BuildArtifacts(
            controller=controller,
            protocol_layout=base / f"protocol_layout{EXE_SUFFIX}",
            ipc_checks=base / f"ipc_safety_checks{EXE_SUFFIX}",
        )
        if artifacts.protocol_layout.exists() and artifacts.ipc_checks.exists():
            return artifacts
    raise RuntimeError("未找到完整 C++ 构建产物，不能使用 --skip-build")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成 Seat Surface AOI Windows 离线部署包")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录，默认 dist/")
    parser.add_argument("--package-name", default="", help="包目录和归档名称，默认 seat-surface-aoi-<git>-<utc>")
    parser.add_argument("--run-tests", action="store_true", help="打包前运行 uv run pytest")
    parser.add_argument("--skip-build", action="store_true", help="跳过 C++ 构建，直接使用现有 build 产物")
    parser.add_argument("--skip-protocol", action="store_true", help="跳过协议和 IPC 诊断校验")
    args = parser.parse_args(argv)

    if not MODEL_DIR.exists():
        print(f"模型目录不存在: {MODEL_DIR}", file=sys.stderr)
        return 2

    try:
        artifacts = _default_existing_artifacts() if args.skip_build else build_cpp()
        _validate_artifacts(artifacts)
    except Exception as exc:
        print(f"打包失败: {exc}", file=sys.stderr)
        return 2

    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT_DIR)
    try:
        if not args.skip_protocol:
            run(_python_runner() + ["-m", "tools.validate_protocol"], env=env)
            run([str(artifacts.protocol_layout)])
            run([str(artifacts.ipc_checks)])
        if args.run_tests:
            run(_pytest_runner(), env=env)
    except subprocess.CalledProcessError as exc:
        print(f"打包前校验失败: {exc}", file=sys.stderr)
        return 1

    output_dir = _resolve_path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_name = args.package_name or f"seat-surface-aoi-{_git_short()}-{created_at}"
    staging_parent = output_dir / ".package-work"
    stage_dir = staging_parent / package_name
    archive_path = output_dir / f"{package_name}.tar.gz"

    if staging_parent.exists():
        shutil.rmtree(staging_parent)
    stage_dir.mkdir(parents=True)

    try:
        bin_dir = stage_dir / "bin"
        bin_dir.mkdir()
        _copy_file(artifacts.controller, bin_dir / artifacts.controller.name)
        _copy_file(artifacts.protocol_layout, bin_dir / artifacts.protocol_layout.name)
        _copy_file(artifacts.ipc_checks, bin_dir / artifacts.ipc_checks.name)

        for dirname in ("cpp_controller", "python_detector", "display_app", "training_tools", "model", "docs"):
            _copy_tree(ROOT_DIR / dirname, stage_dir / dirname)

        tools_dir = stage_dir / "tools"
        tools_dir.mkdir()
        for filename in (
            "run_simulated_ipc.py",
            "run_cpp_soak.py",
            "validate_protocol.py",
            "validate_model_assets.py",
            "validate_architecture_readiness.py",
            "validate_deployment_preflight.py",
            "package_release.py",
            "package_python_offline_deps.py",
        ):
            _copy_file(ROOT_DIR / "tools" / filename, tools_dir / filename)

        for filename in ("README.md", "AGENTS.md", "pyproject.toml"):
            _copy_file(ROOT_DIR / filename, stage_dir / filename)
        uv_lock = ROOT_DIR / "uv.lock"
        if uv_lock.exists():
            _copy_file(uv_lock, stage_dir / "uv.lock")

        _write_package_readme(stage_dir)
        _write_validate_package(stage_dir)
        _write_packaged_ipc(stage_dir)
        _write_manifest(stage_dir, package_name, created_at, MODEL_DIR)
        _write_file_list(stage_dir)
        _make_archive(stage_dir, archive_path)
        _write_sha256(archive_path)
    finally:
        if staging_parent.exists():
            shutil.rmtree(staging_parent)

    digest = archive_path.with_suffix(archive_path.suffix + ".sha256").read_text(encoding="utf-8").split()[0]
    print(f"部署包已生成: {archive_path}")
    print(f"SHA256: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
