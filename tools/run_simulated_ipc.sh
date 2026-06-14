#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/cpp_controller/build"
CONTROLLER="${BUILD_DIR}/seat_aoi_controller"
IPC_CHECKS="${BUILD_DIR}/ipc_safety_checks"
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

mkdir -p "${BUILD_DIR}"
if command -v uv >/dev/null 2>&1; then
  PYTHON_RUNNER=(uv run python)
else
  PYTHON_RUNNER=(python3)
fi

if command -v cmake >/dev/null 2>&1; then
  cmake -S "${ROOT_DIR}/cpp_controller" -B "${BUILD_DIR}"
  cmake --build "${BUILD_DIR}"
elif command -v clang++ >/dev/null 2>&1; then
  clang++ -std=c++17 -I "${ROOT_DIR}/cpp_controller/include" \
    "${ROOT_DIR}/cpp_controller/src/main.cpp" \
    "${ROOT_DIR}/cpp_controller/src/ipc/crc32.cpp" \
    "${ROOT_DIR}/cpp_controller/src/ipc/shared_memory_posix.cpp" \
    "${ROOT_DIR}/cpp_controller/src/ipc/frame_ring_buffer.cpp" \
    "${ROOT_DIR}/cpp_controller/src/ipc/result_ring_buffer.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/light_controller.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/plc_client.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/robot_client.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/production_event_log.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/station_health.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/station_runtime_config.cpp" \
    "${ROOT_DIR}/cpp_controller/src/camera/camera_device.cpp" \
    "${ROOT_DIR}/cpp_controller/src/camera/camera_worker.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/trigger_scheduler.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/frame_assembler.cpp" \
    "${ROOT_DIR}/cpp_controller/src/control/station_controller.cpp" \
    -o "${CONTROLLER}"
	  clang++ -std=c++17 -I "${ROOT_DIR}/cpp_controller/include" \
	    "${ROOT_DIR}/cpp_controller/tools/ipc_safety_checks.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/ipc/crc32.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/ipc/shared_memory_posix.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/ipc/frame_ring_buffer.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/ipc/result_ring_buffer.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/light_controller.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/plc_client.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/robot_client.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/production_event_log.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/station_health.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/station_runtime_config.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/camera/camera_device.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/camera/camera_worker.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/trigger_scheduler.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/frame_assembler.cpp" \
	    "${ROOT_DIR}/cpp_controller/src/control/station_controller.cpp" \
	    -o "${IPC_CHECKS}"
else
  echo "缺少 cmake 或 clang++，无法构建 C++ 主控。" >&2
  exit 2
fi

"${IPC_CHECKS}"
"${CONTROLLER}" --cleanup >/dev/null 2>&1 || true
CONTROLLER_ARGS=(--once --wait-ms 8000)
if [[ -n "${CONFIG_PATH}" ]]; then
  CONTROLLER_ARGS=(--config "${CONFIG_PATH}" "${CONTROLLER_ARGS[@]}")
fi
"${CONTROLLER}" "${CONTROLLER_ARGS[@]}" &
CPP_PID=$!
sleep 0.2

(
  cd "${ROOT_DIR}"
  PYTHONPATH="${ROOT_DIR}" "${PYTHON_RUNNER[@]}" -m python_detector.detector_main --once --timeout-ms 8000
)

wait "${CPP_PID}"
"${CONTROLLER}" --cleanup >/dev/null 2>&1 || true
