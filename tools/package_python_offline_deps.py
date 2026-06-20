from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "dist"
PROJECT_NAME = "seat-surface-aoi-python-detector"


def main() -> int:
    args = _parse_args()
    output_dir = _resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    created_at = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    package_name = args.package_name or _default_package_name(created_at, args.python_version)
    stage_dir = output_dir / package_name
    archive_path = output_dir / f"{package_name}.zip"

    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    if archive_path.exists():
        archive_path.unlink()

    try:
        wheelhouse = stage_dir / "wheelhouse"
        wheelhouse.mkdir(parents=True)

        requirements_path = stage_dir / "requirements.txt"
        _export_requirements(
            requirements_path=requirements_path,
            extras=args.extra,
            groups=args.group,
            all_extras=args.all_extras,
            include_dev=args.include_dev,
        )
        project_wheel = _build_project_wheel(wheelhouse)
        _download_wheels(
            requirements_path=requirements_path,
            wheelhouse=wheelhouse,
            python_version=args.python_version,
            target_platform=args.target_platform,
        )

        _write_install_scripts(stage_dir, args.python_version)
        _write_readme(stage_dir, args)
        _write_manifest(stage_dir, args, package_name, created_at, project_wheel)
        _write_file_list(stage_dir)
        _zip_directory(stage_dir, archive_path)
        _write_sha256(archive_path)
    except Exception:
        if stage_dir.exists():
            shutil.rmtree(stage_dir)
        raise

    print(f"Python 离线依赖包已生成: {archive_path}")
    print(f"SHA256: {archive_path.with_suffix(archive_path.suffix + '.sha256').read_text(encoding='utf-8').split()[0]}")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="生成 Windows 工控机离线恢复 Python 依赖所需的 wheelhouse 和安装脚本。",
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="输出目录，默认 dist/。")
    parser.add_argument("--package-name", default="", help="zip 包名，默认自动包含 git、平台和时间。")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        choices=["display", "onnx", "faiss"],
        help="包含 pyproject.toml 中的 optional extra，可重复指定。",
    )
    parser.add_argument("--all-extras", action="store_true", help="包含全部 optional extras。")
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        choices=["dev", "test", "training"],
        help="包含 dependency group，可重复指定；生产包通常不需要 training。",
    )
    parser.add_argument(
        "--include-dev",
        action="store_true",
        help="保留 uv 默认开发依赖组；默认使用 --no-dev 生成生产运行依赖。",
    )
    parser.add_argument(
        "--python-version",
        default=_default_python_version(),
        help="目标 Python 主次版本，默认读取 .python-version 或当前解释器版本。",
    )
    parser.add_argument(
        "--target-platform",
        default="",
        help=(
            "可选 pip 平台标签，例如 win_amd64。留空表示按当前开发机平台下载，"
            "推荐在与工控机同 OS/CPU/Python 的机器上生成。"
        ),
    )
    return parser.parse_args()


def _resolve_output_dir(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _default_python_version() -> str:
    version_file = REPO_ROOT / ".python-version"
    if version_file.exists():
        value = version_file.read_text(encoding="utf-8").strip()
        if value:
            return ".".join(value.split(".")[:2])
    return f"{sys.version_info.major}.{sys.version_info.minor}"


def _default_package_name(created_at: str, python_version: str) -> str:
    git_short = _git_short() or "nogit"
    system = platform.system().lower() or "unknown"
    machine = platform.machine().lower() or "unknown"
    return f"seat-surface-aoi-python-offline-deps-{git_short}-{system}-{machine}-py{python_version}-{created_at}"


def _git_short() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def _export_requirements(
    *,
    requirements_path: Path,
    extras: list[str],
    groups: list[str],
    all_extras: bool,
    include_dev: bool,
) -> None:
    command = [
        "uv",
        "export",
        "--format",
        "requirements.txt",
        "--frozen",
        "--no-hashes",
        "--no-emit-project",
        "--output-file",
        str(requirements_path),
    ]
    if not include_dev:
        command.append("--no-dev")
    if all_extras:
        command.append("--all-extras")
    for extra in extras:
        command.extend(["--extra", extra])
    for group in groups:
        command.extend(["--group", group])
    _run(command)


def _build_project_wheel(wheelhouse: Path) -> Path:
    before = {path.name for path in wheelhouse.glob("*.whl")}
    _run(["uv", "build", "--wheel", "--out-dir", str(wheelhouse), str(REPO_ROOT)])
    wheels = sorted(path for path in wheelhouse.glob("*.whl") if path.name not in before)
    if not wheels:
        wheels = sorted(wheelhouse.glob("*.whl"))
    project_wheels = [path for path in wheels if path.name.replace("-", "_").startswith(PROJECT_NAME.replace("-", "_"))]
    if not project_wheels:
        raise RuntimeError("未找到当前项目 wheel，无法生成离线安装包。")
    return project_wheels[-1]


def _download_wheels(
    *,
    requirements_path: Path,
    wheelhouse: Path,
    python_version: str,
    target_platform: str,
) -> None:
    downloader_env = wheelhouse.parent / ".pip-download-env"
    if downloader_env.exists():
        shutil.rmtree(downloader_env)
    try:
        _run([sys.executable, "-m", "venv", "--without-pip", str(downloader_env)])
        downloader_python = _venv_python(downloader_env)
        _run(["uv", "pip", "install", "--python", str(downloader_python), "pip"])
        pip_check = subprocess.run(
            [str(downloader_python), "-m", "pip", "--version"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        if pip_check.returncode != 0:
            _run([str(downloader_python), "-m", "ensurepip", "--upgrade"])
        _pip_download(
            python_executable=downloader_python,
            requirements_path=requirements_path,
            wheelhouse=wheelhouse,
            python_version=python_version,
            target_platform=target_platform,
        )
    finally:
        if downloader_env.exists():
            shutil.rmtree(downloader_env)


def _venv_python(venv_path: Path) -> Path:
    if platform.system().lower() == "windows":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def _pip_download(
    *,
    python_executable: Path,
    requirements_path: Path,
    wheelhouse: Path,
    python_version: str,
    target_platform: str,
) -> None:
    command = [
        str(python_executable),
        "-m",
        "pip",
        "download",
        "--requirement",
        str(requirements_path),
        "--dest",
        str(wheelhouse),
        "--only-binary",
        ":all:",
    ]
    if target_platform:
        python_tag = python_version.replace(".", "")
        command.extend(
            [
                "--platform",
                target_platform,
                "--python-version",
                python_version,
                "--implementation",
                "cp",
                "--abi",
                f"cp{python_tag}",
            ]
        )
    _run(command)


def _write_install_scripts(stage_dir: Path, python_version: str) -> None:
    (stage_dir / "install_offline.ps1").write_text(
        _powershell_installer(python_version),
        encoding="utf-8-sig",
        newline="\n",
    )


def _powershell_installer(python_version: str) -> str:
    template = r'''param(
  [string]$ProjectRoot = (Get-Location).Path,
  [string]$VenvName = ".venv",
  [string]$PythonVersion = "__PYTHON_VERSION__",
  [string]$PythonExe = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Native {
  param([Parameter(ValueFromRemainingArguments=$true)][string[]]$Command)
  & $Command[0] @($Command | Select-Object -Skip 1)
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $($Command -join ' ')"
  }
}

function Assert-PythonVersion {
  param([string]$PythonPath)
  $Actual = & $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to read Python version from $PythonPath"
  }
  if ($Actual.Trim() -ne $PythonVersion) {
    throw "Python version mismatch: expected $PythonVersion, got $($Actual.Trim()). Rebuild the offline package for this Python version or install Python $PythonVersion."
  }
}

$DepsRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Wheelhouse = Join-Path $DepsRoot "wheelhouse"
$Requirements = Join-Path $DepsRoot "requirements.txt"
$VenvPath = Join-Path $ProjectRoot $VenvName

if (-not (Test-Path $Wheelhouse)) { throw "Missing wheelhouse: $Wheelhouse" }
if (-not (Test-Path $Requirements)) { throw "Missing requirements.txt: $Requirements" }
$ProjectWheel = Get-ChildItem -Path $Wheelhouse -Filter "seat_surface_aoi_python_detector-*.whl" |
  Sort-Object Name |
  Select-Object -First 1
if ($null -eq $ProjectWheel) { throw "Missing project wheel in wheelhouse: $Wheelhouse" }

if ($PythonExe) {
  Invoke-Native $PythonExe -m venv $VenvPath
} elseif ($env:PYTHON) {
  Invoke-Native $env:PYTHON -m venv $VenvPath
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  Invoke-Native py "-$PythonVersion" -m venv $VenvPath
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
  Invoke-Native uv venv $VenvPath --python $PythonVersion --offline
} else {
  Invoke-Native python -m venv $VenvPath
}

$VenvPython = Join-Path $VenvPath "Scripts\python.exe"
Assert-PythonVersion $VenvPython
$PipProbe = & $VenvPython -m pip --version 2>$null
if ($LASTEXITCODE -eq 0) {
  Invoke-Native $VenvPython -m pip install --no-index --find-links $Wheelhouse --requirement $Requirements
  Invoke-Native $VenvPython -m pip install --no-index --find-links $Wheelhouse $ProjectWheel.FullName
  Invoke-Native $VenvPython -m pip check
} elseif (Get-Command uv -ErrorAction SilentlyContinue) {
  Invoke-Native uv pip install --offline --python $VenvPython --find-links $Wheelhouse --requirement $Requirements
  Invoke-Native uv pip install --offline --python $VenvPython --find-links $Wheelhouse $ProjectWheel.FullName
  Invoke-Native uv pip check --python $VenvPython
} else {
  throw "Target venv has no pip and uv was not found. Ship uv.exe with the offline package or use a Python installer with ensurepip."
}
Write-Host "Python offline environment created: $VenvPath"
'''
    return template.replace("__PYTHON_VERSION__", python_version)


def _write_readme(stage_dir: Path, args: argparse.Namespace) -> None:
    extras = ", ".join(args.extra) if args.extra else "无"
    groups = ", ".join(args.group) if args.group else "无"
    text = f"""# Seat Surface AOI Python 离线依赖包

本包用于 Windows 工控机无公网时恢复 Python detector/display 运行环境。它不复制开发机 `.venv`，而是在目标机用本地 wheelhouse 新建虚拟环境。

## 生成参数

- Python 版本：{args.python_version}
- extras：{extras}
- dependency groups：{groups}
- all extras：{args.all_extras}
- include dev：{args.include_dev}
- target platform：{args.target_platform or "当前开发机平台"}

## 工控机安装

1. 解压 `uv run python -m tools.package_release` 生成的项目发布包。
2. 把本离线依赖包解压到发布目录旁边或发布目录下，例如 `offline_python_deps/`。
3. 在发布目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\\offline_python_deps\\install_offline.ps1 -ProjectRoot .
```

安装完成后使用发布目录内的新 `.venv` 运行：

```powershell
.\\.venv\\Scripts\\python.exe -m tools.validate_protocol
.\\.venv\\Scripts\\python.exe -m tools.validate_deployment_preflight
.\\.venv\\Scripts\\python.exe -m python_detector.detector_main --once --timeout-ms 8000
```

生产推理如果需要 ONNX Runtime、FAISS 或展示端，生成本包时必须追加对应参数：

```powershell
uv run python -m tools.package_python_offline_deps --extra onnx --extra faiss --extra display
```

建议在与工控机相同的 Windows 版本、CPU 架构和 Python 主次版本上生成本包。真实相机 SDK、PLC/IO 驱动、频闪控制器 DLL、VC++ Runtime 和模型资产仍需按现场单独交付。
"""
    (stage_dir / "OFFLINE_DEPS_README.md").write_text(text, encoding="utf-8", newline="\n")


def _write_manifest(
    stage_dir: Path,
    args: argparse.Namespace,
    package_name: str,
    created_at: str,
    project_wheel: Path,
) -> None:
    manifest = {
        "package_name": package_name,
        "created_at_utc": created_at,
        "project": PROJECT_NAME,
        "project_wheel": project_wheel.name,
        "python_version": args.python_version,
        "target_platform": args.target_platform or "current",
        "extras": args.extra,
        "all_extras": args.all_extras,
        "groups": args.group,
        "include_dev": args.include_dev,
        "host": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
    }
    (stage_dir / "OFFLINE_DEPS_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip()


def _git_dirty() -> bool:
    try:
        diff = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "diff", "--quiet", "--ignore-submodules", "--"],
            check=False,
        )
        cached = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "diff", "--cached", "--quiet", "--ignore-submodules", "--"],
            check=False,
        )
    except FileNotFoundError:
        return False
    return diff.returncode != 0 or cached.returncode != 0


def _write_file_list(stage_dir: Path) -> None:
    files = sorted(path.relative_to(stage_dir).as_posix() for path in stage_dir.rglob("*") if path.is_file())
    (stage_dir / "OFFLINE_DEPS_FILES.txt").write_text("\n".join(files) + "\n", encoding="utf-8")


def _zip_directory(stage_dir: Path, archive_path: Path) -> None:
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(stage_dir.parent))


def _write_sha256(path: Path) -> None:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{digest.hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )


def _run(command: list[str]) -> None:
    print("+ " + " ".join(command))
    subprocess.run(command, cwd=REPO_ROOT, check=True)


if __name__ == "__main__":
    raise SystemExit(main())
