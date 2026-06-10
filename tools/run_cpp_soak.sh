#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="${ROOT_DIR}/cpp_controller/build"
CONTROLLER="${BUILD_DIR}/seat_aoi_controller"
JOBS=20
WAIT_MS=8000
TRACE_ROOT="${ROOT_DIR}/trace/cpp_soak"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --jobs)
      JOBS="$2"
      shift 2
      ;;
    --wait-ms)
      WAIT_MS="$2"
      shift 2
      ;;
    --trace-root)
      TRACE_ROOT="$2"
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

cmake -S "${ROOT_DIR}/cpp_controller" -B "${BUILD_DIR}"
cmake --build "${BUILD_DIR}"

rm -rf "${TRACE_ROOT}"
mkdir -p "${TRACE_ROOT}"
"${CONTROLLER}" --cleanup >/dev/null 2>&1 || true

ok_count=0
failed_count=0
start_s="$(date +%s)"

for ((i = 1; i <= JOBS; ++i)); do
  "${CONTROLLER}" --once --wait-ms "${WAIT_MS}" --trace-root "${TRACE_ROOT}" &
  cpp_pid=$!
  sleep 0.2
  if (
    cd "${ROOT_DIR}"
    PYTHONPATH="${ROOT_DIR}" "${PYTHON_RUNNER[@]}" -m python_detector.detector_main \
      --once --timeout-ms "${WAIT_MS}"
  ); then
    :
  else
    echo "detector failed at job ${i}" >&2
  fi
  if wait "${cpp_pid}"; then
    ok_count=$((ok_count + 1))
  else
    failed_count=$((failed_count + 1))
  fi
done

"${CONTROLLER}" --cleanup >/dev/null 2>&1 || true
elapsed_s=$(( $(date +%s) - start_s ))

event_log="${TRACE_ROOT}/cpp_controller_events.jsonl"
summary="${TRACE_ROOT}/summary.txt"
{
  echo "jobs=${JOBS}"
  echo "ok_iterations=${ok_count}"
  echo "failed_iterations=${failed_count}"
  echo "elapsed_s=${elapsed_s}"
  if [[ -f "${event_log}" ]]; then
    echo "event_log=${event_log}"
    echo "recent_events:"
    tail -n 20 "${event_log}"
  fi
} | tee "${summary}"

if [[ "${failed_count}" -ne 0 ]]; then
  exit 1
fi
