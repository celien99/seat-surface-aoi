#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/cpp_controller/build"
OUTPUT_DIR="${ROOT_DIR}/dist"
MODEL_DIR="${ROOT_DIR}/model"
PACKAGE_NAME=""
CMAKE_BUILD_TYPE="Release"
RUN_TESTS=0
SKIP_BUILD=0
SKIP_PROTOCOL=0

usage() {
  cat <<'USAGE'
生成 Seat Surface AOI 离线部署包。

默认包内容：
  - bin/：已构建的 C++ 主控与 IPC 诊断工具
  - cpp_controller/：C++ 主控源码、配置和 CMake 工程
  - python_detector/：Python 在线检测进程、配方、标定和测试
  - display_app/：PySide6/QML 展示前端源码和样式资源
  - training_tools/：离线回放、benchmark 和模型资产生成工具
  - model/：根目录 model/ 下的模型目录结构或真实模型产物
  - tools/：协议、模型资产、架构检查和模拟 IPC 脚本
  - docs/、README.md、pyproject.toml、uv.lock

用法:
  bash tools/package_release.sh [选项]

选项:
  --output-dir <path>        输出目录，默认 dist/
  --package-name <name>      包目录和归档名称，默认 seat-surface-aoi-<git>-<utc>
  --run-tests                打包前运行 uv run pytest
  --skip-build               跳过 C++ 构建，直接使用现有 build 产物
  --skip-protocol            跳过协议和 IPC 诊断校验
  --help                     显示帮助

生产打包建议:
  先把真实模型产物替换到根目录 model/，再执行 bash tools/package_release.sh。
USAGE
}

fail() {
  echo "打包失败: $*" >&2
  exit 2
}

abs_path() {
  local path="$1"
  if [[ "${path}" == /* ]]; then
    printf '%s\n' "${path}"
  else
    printf '%s\n' "${ROOT_DIR}/${path}"
  fi
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="$(abs_path "$2")"
      shift 2
      ;;
    --package-name)
      PACKAGE_NAME="$2"
      shift 2
      ;;
    --run-tests)
      RUN_TESTS=1
      shift
      ;;
    --skip-build)
      SKIP_BUILD=1
      shift
      ;;
    --skip-protocol)
      SKIP_PROTOCOL=1
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      fail "未知参数: $1"
      ;;
  esac
done

[[ -d "${MODEL_DIR}" ]] || fail "模型目录不存在: ${MODEL_DIR}"

if command -v uv >/dev/null 2>&1; then
  PYTHON_RUNNER=(uv run python)
  PYTEST_RUNNER=(uv run pytest)
else
  PYTHON_RUNNER=(python3)
  PYTEST_RUNNER=(python3 -m pytest)
fi

build_cpp_with_clang() {
  command -v clang++ >/dev/null 2>&1 || fail "缺少 cmake 和 clang++，无法构建 C++ 主控"
  mkdir -p "${BUILD_DIR}"
  local include_args=(-std=c++17 -O2 -I "${ROOT_DIR}/cpp_controller/include")
  local common_sources=(
    "${ROOT_DIR}/cpp_controller/src/ipc/crc32.cpp"
    "${ROOT_DIR}/cpp_controller/src/ipc/shared_memory_posix.cpp"
    "${ROOT_DIR}/cpp_controller/src/ipc/frame_ring_buffer.cpp"
    "${ROOT_DIR}/cpp_controller/src/ipc/result_ring_buffer.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/hardware_backend.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/fl_acdh_light_controller.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/light_controller.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/signal_client.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/tcp_signal_client.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/robot_client.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/production_event_log.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/station_health.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/station_runtime_config.cpp"
    "${ROOT_DIR}/cpp_controller/src/camera/camera_device.cpp"
    "${ROOT_DIR}/cpp_controller/src/camera/hikrobot_mvs_camera.cpp"
    "${ROOT_DIR}/cpp_controller/src/camera/camera_worker.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/trigger_scheduler.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/frame_assembler.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/station_controller.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/image_writer.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/distance_sensor.cpp"
    "${ROOT_DIR}/cpp_controller/src/control/distance_trigger_signal_client.cpp"
  )
  clang++ "${include_args[@]}" "${ROOT_DIR}/cpp_controller/src/main.cpp" "${common_sources[@]}" \
    -o "${BUILD_DIR}/seat_aoi_controller"
  clang++ "${include_args[@]}" "${ROOT_DIR}/cpp_controller/tools/ipc_safety_checks.cpp" "${common_sources[@]}" \
    -o "${BUILD_DIR}/ipc_safety_checks"
  clang++ "${include_args[@]}" "${ROOT_DIR}/cpp_controller/tools/protocol_layout.cpp" \
    -o "${BUILD_DIR}/protocol_layout"
}

build_cpp() {
  mkdir -p "${BUILD_DIR}"
  if command -v cmake >/dev/null 2>&1; then
    cmake -S "${ROOT_DIR}/cpp_controller" -B "${BUILD_DIR}" -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}"
    cmake --build "${BUILD_DIR}" --config "${CMAKE_BUILD_TYPE}"
  else
    build_cpp_with_clang
  fi
}

ensure_binaries() {
  local required=(
    "${BUILD_DIR}/seat_aoi_controller"
    "${BUILD_DIR}/protocol_layout"
    "${BUILD_DIR}/ipc_safety_checks"
  )
  for binary in "${required[@]}"; do
    [[ -x "${binary}" ]] || fail "缺少可执行构建产物: ${binary}"
  done
}

copy_tree() {
  local source_dir="$1"
  local target_dir="$2"
  mkdir -p "${target_dir}"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '__pycache__/' \
      --exclude '*.pyc' \
      --exclude '.pytest_cache/' \
      --exclude '.mypy_cache/' \
      --exclude '.ruff_cache/' \
      --exclude '.DS_Store' \
      --exclude 'build/' \
      --exclude 'dist/' \
      "${source_dir}/" "${target_dir}/"
  else
    (
      cd "${source_dir}"
      tar \
        --exclude './__pycache__' \
        --exclude './*/__pycache__' \
        --exclude './*.pyc' \
        --exclude './.pytest_cache' \
        --exclude './.mypy_cache' \
        --exclude './.ruff_cache' \
        --exclude './.DS_Store' \
        --exclude './build' \
        --exclude './dist' \
        -cf - .
    ) | (
      cd "${target_dir}"
      tar -xf -
    )
  fi
}

write_package_readme() {
  local stage_dir="$1"
  cat > "${stage_dir}/PACKAGE_README.md" <<'EOF'
# Seat Surface AOI 离线部署包

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

```bash
bash validate_package.sh
```

如果需要跑端到端模拟 IPC：

```bash
bash tools/run_simulated_ipc.sh
uv run python tools/run_simulated_ipc.py
```

上 Windows 工控机或产线联调前，先运行部署预检：

```bash
PYTHONPATH=. uv run python -m tools.validate_deployment_preflight
PYTHONPATH=. uv run python -m tools.validate_deployment_preflight --strict-production
```

默认预检用于交接，会把真实模型、正式 production.conf、生产光源/配方对齐和 MES/监控接口列为现场 ACTION；`--strict-production` 用于上机放行前，会把真实模型、正式生产配置缺失和光源/配方不一致作为阻塞项。

## 生产模型

生产包必须先把真实模型产物放入 `model/`，打包脚本会默认集成该目录。占位模型只能用于参考链路和联调包，不能作为生产包放行。

## 启动入口

```bash
# C++ 主控，解包后可直接使用 bin/ 内已构建产物
./bin/seat_aoi_controller --config cpp_controller/config/station_runtime.example.conf --once --wait-ms 8000

# Python detector
PYTHONPATH=. uv run python -m python_detector.detector_main --once --timeout-ms 8000

# PySide6/QML 展示前端，需要安装 display extra
uv sync --extra display
PYTHONPATH=. uv run seat-aoi-display --trace-root trace --line-id AOI-1
```

在线图像和检测结果只能通过共享内存交换，不使用 TCP；Windows 工控机使用 Named Shared Memory。任何缺帧、超时、协议错误、CRC 错误、质量门禁失败或模型异常都不能输出 OK。
EOF
}

write_validate_script() {
  local stage_dir="$1"
  cat > "${stage_dir}/validate_package.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if python3 -c "import yaml" >/dev/null 2>&1; then
  PYTHON_RUNNER=(python3)
elif command -v uv >/dev/null 2>&1; then
  PYTHON_RUNNER=(uv run python)
else
  PYTHON_RUNNER=(python3)
fi

PYTHONPATH="${ROOT_DIR}" "${PYTHON_RUNNER[@]}" -m tools.validate_protocol
PYTHONPATH="${ROOT_DIR}" "${PYTHON_RUNNER[@]}" -m tools.validate_deployment_preflight
"${ROOT_DIR}/bin/protocol_layout"
"${ROOT_DIR}/bin/ipc_safety_checks"
echo "部署包基础校验通过"
EOF
  chmod +x "${stage_dir}/validate_package.sh"
}

write_packaged_ipc_script() {
  local stage_dir="$1"
  cat > "${stage_dir}/run_packaged_simulated_ipc.sh" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_PATH=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config)
      CONFIG_PATH="$2"
      shift 2
      ;;
    *)
      echo "未知参数: $1" >&2
      exit 2
      ;;
  esac
done

if command -v uv >/dev/null 2>&1; then
  PYTHON_RUNNER=(uv run python)
else
  PYTHON_RUNNER=(python3)
fi

CONTROLLER="${ROOT_DIR}/bin/seat_aoi_controller"
IPC_CHECKS="${ROOT_DIR}/bin/ipc_safety_checks"
[[ -x "${CONTROLLER}" ]] || { echo "缺少 C++ 主控: ${CONTROLLER}" >&2; exit 2; }
[[ -x "${IPC_CHECKS}" ]] || { echo "缺少 IPC 诊断工具: ${IPC_CHECKS}" >&2; exit 2; }

"${IPC_CHECKS}"
"${CONTROLLER}" --cleanup >/dev/null 2>&1 || true
CONTROLLER_ARGS=(--once --wait-ms 8000)
DETECTOR_ARGS=(--once --timeout-ms 8000)
if [[ -n "${CONFIG_PATH}" ]]; then
  CONTROLLER_ARGS=(--config "${CONFIG_PATH}" "${CONTROLLER_ARGS[@]}")
  DETECTOR_ARGS=(--config "${CONFIG_PATH}" "${DETECTOR_ARGS[@]}")
fi
"${CONTROLLER}" "${CONTROLLER_ARGS[@]}" &
CPP_PID=$!
sleep 0.2

(
  cd "${ROOT_DIR}"
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_RUNNER[@]}" -m python_detector.detector_main "${DETECTOR_ARGS[@]}"
)

wait "${CPP_PID}"
"${CONTROLLER}" --cleanup >/dev/null 2>&1 || true
EOF
  chmod +x "${stage_dir}/run_packaged_simulated_ipc.sh"
}

write_manifest() {
  local stage_dir="$1"
  local created_at="$2"
  local git_commit="$3"
  local git_dirty="$4"
  local model_dir="$5"
  local uname_value
  uname_value="$(uname -a)"
  cat > "${stage_dir}/PACKAGE_MANIFEST.json" <<EOF
{
  "package_name": "${PACKAGE_NAME}",
  "created_at_utc": "${created_at}",
  "git_commit": "${git_commit}",
  "git_dirty": ${git_dirty},
  "platform": "${uname_value}",
  "build_type": "${CMAKE_BUILD_TYPE}",
  "model_dir": "${model_dir}",
  "components": [
    "bin/seat_aoi_controller",
    "bin/protocol_layout",
    "bin/ipc_safety_checks",
    "cpp_controller",
    "python_detector",
    "display_app",
    "training_tools",
    "model",
    "tools",
    "docs"
  ]
}
EOF
}

if [[ "${SKIP_BUILD}" -eq 0 ]]; then
  build_cpp
fi
ensure_binaries

if [[ "${SKIP_PROTOCOL}" -eq 0 ]]; then
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_RUNNER[@]}" -m tools.validate_protocol
  "${BUILD_DIR}/protocol_layout" >/dev/null
  "${BUILD_DIR}/ipc_safety_checks"
fi

if [[ "${RUN_TESTS}" -eq 1 ]]; then
  PYTHONPATH="${ROOT_DIR}" "${PYTEST_RUNNER[@]}"
fi

mkdir -p "${OUTPUT_DIR}"
GIT_SHORT="$(git -C "${ROOT_DIR}" rev-parse --short HEAD 2>/dev/null || true)"
if [[ -z "${GIT_SHORT}" ]]; then
  GIT_SHORT="nogit"
fi
CREATED_AT="$(date -u +%Y%m%dT%H%M%SZ)"
if [[ -z "${PACKAGE_NAME}" ]]; then
  PACKAGE_NAME="seat-surface-aoi-${GIT_SHORT}-${CREATED_AT}"
fi

STAGING_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/seat-aoi-package.XXXXXX")"
STAGE_DIR="${STAGING_PARENT}/${PACKAGE_NAME}"
ARCHIVE_PATH="${OUTPUT_DIR}/${PACKAGE_NAME}.tar.gz"
mkdir -p "${STAGE_DIR}/bin"
trap 'rm -rf "${STAGING_PARENT}"' EXIT

cp "${BUILD_DIR}/seat_aoi_controller" "${STAGE_DIR}/bin/"
cp "${BUILD_DIR}/protocol_layout" "${STAGE_DIR}/bin/"
cp "${BUILD_DIR}/ipc_safety_checks" "${STAGE_DIR}/bin/"

copy_tree "${ROOT_DIR}/cpp_controller" "${STAGE_DIR}/cpp_controller"
copy_tree "${ROOT_DIR}/python_detector" "${STAGE_DIR}/python_detector"
copy_tree "${ROOT_DIR}/display_app" "${STAGE_DIR}/display_app"
copy_tree "${ROOT_DIR}/training_tools" "${STAGE_DIR}/training_tools"
copy_tree "${MODEL_DIR}" "${STAGE_DIR}/model"
if [[ ! -f "${STAGE_DIR}/model/README.md" && -f "${ROOT_DIR}/model/README.md" ]]; then
  cp "${ROOT_DIR}/model/README.md" "${STAGE_DIR}/model/README.md"
fi
copy_tree "${ROOT_DIR}/docs" "${STAGE_DIR}/docs"

mkdir -p "${STAGE_DIR}/tools"
cp "${ROOT_DIR}/tools/run_simulated_ipc.sh" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/run_simulated_ipc.py" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/run_cpp_soak.sh" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/validate_protocol.py" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/validate_model_assets.py" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/validate_architecture_readiness.py" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/validate_deployment_preflight.py" "${STAGE_DIR}/tools/"
cp "${ROOT_DIR}/tools/package_release.sh" "${STAGE_DIR}/tools/"
chmod +x "${STAGE_DIR}/tools/"*.sh

cp "${ROOT_DIR}/README.md" "${STAGE_DIR}/README.md"
cp "${ROOT_DIR}/AGENTS.md" "${STAGE_DIR}/AGENTS.md"
cp "${ROOT_DIR}/pyproject.toml" "${STAGE_DIR}/pyproject.toml"
if [[ -f "${ROOT_DIR}/uv.lock" ]]; then
  cp "${ROOT_DIR}/uv.lock" "${STAGE_DIR}/uv.lock"
fi

write_package_readme "${STAGE_DIR}"
write_validate_script "${STAGE_DIR}"
write_packaged_ipc_script "${STAGE_DIR}"

GIT_COMMIT="$(git -C "${ROOT_DIR}" rev-parse HEAD 2>/dev/null || true)"
if [[ -z "${GIT_COMMIT}" ]]; then
  GIT_COMMIT="unknown"
fi
if git -C "${ROOT_DIR}" diff --quiet --ignore-submodules -- && git -C "${ROOT_DIR}" diff --cached --quiet --ignore-submodules --; then
  GIT_DIRTY=false
else
  GIT_DIRTY=true
fi
write_manifest "${STAGE_DIR}" "${CREATED_AT}" "${GIT_COMMIT}" "${GIT_DIRTY}" "${MODEL_DIR}"

(
  cd "${STAGE_DIR}"
  find . -type f | sort > PACKAGE_FILES.txt
)

rm -rf "${STAGE_DIR}/.venv" "${STAGE_DIR}/__pycache__"

rm -f "${ARCHIVE_PATH}" "${ARCHIVE_PATH}.sha256"
(
  cd "${STAGING_PARENT}"
  tar -czf "${ARCHIVE_PATH}" "${PACKAGE_NAME}"
)
if command -v shasum >/dev/null 2>&1; then
  shasum -a 256 "${ARCHIVE_PATH}" > "${ARCHIVE_PATH}.sha256"
elif command -v sha256sum >/dev/null 2>&1; then
  sha256sum "${ARCHIVE_PATH}" > "${ARCHIVE_PATH}.sha256"
else
  fail "缺少 shasum 或 sha256sum，无法生成校验文件"
fi

echo "部署包已生成: ${ARCHIVE_PATH}"
echo "SHA256: $(cut -d ' ' -f 1 "${ARCHIVE_PATH}.sha256")"
