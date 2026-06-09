from __future__ import annotations

from pathlib import Path
from typing import Any

from python_detector.ipc.data_types import InspectionResult


def quality_reasons(context: dict[str, Any], limit: int = 5) -> list[str]:
    report = context.get("quality_report")
    if report is None:
        return []

    reasons: list[str] = []
    reasons.extend(str(message) for message in getattr(report, "messages", []) or [])
    for frame_report in getattr(report, "frame_reports", []) or []:
        frame_messages = getattr(frame_report, "messages", []) or []
        if not frame_messages:
            continue
        camera_id = getattr(frame_report, "camera_id", "")
        light_id = getattr(frame_report, "light_id", "")
        reasons.append(f"{camera_id}/{light_id}: {', '.join(str(message) for message in frame_messages)}")
    return reasons[: max(limit, 0)]


def error_reason(context: dict[str, Any]) -> str:
    error = context.get("error") or {}
    if not isinstance(error, dict):
        return ""
    error_type = str(error.get("type") or "").strip()
    message = str(error.get("message") or "").strip()
    if error_type and message:
        return f"{error_type}: {message}"
    return error_type or message


def format_replay_line(
    result: InspectionResult,
    context: dict[str, Any],
    trace_dir: Path | None = None,
    summary_limit: int = 5,
) -> str:
    parts = [
        f"sequence_id={result.sequence_id}",
        f"decision={result.decision}",
        f"quality_pass={result.quality_pass}",
        f"error_code={int(result.error_code)}",
        f"defects={len(result.defects)}",
        f"elapsed_ms={result.elapsed_ms:.2f}",
    ]
    reasons = quality_reasons(context, summary_limit)
    if reasons:
        parts.append(f"quality_reasons={_join_summary(reasons)}")
    error = error_reason(context)
    if error:
        parts.append(f"error={_quote(error)}")
    if trace_dir is not None:
        parts.append(f"trace_dir={trace_dir}")
    return " ".join(parts)


def collect_timing_samples(samples: list[dict[str, float]], context: dict[str, Any]) -> None:
    timings = context.get("timings") or {}
    if not isinstance(timings, dict):
        return
    samples.append({str(key): float(value) for key, value in timings.items() if isinstance(value, (int, float))})


def format_benchmark_report(count: int, totals: list[float], timing_samples: list[dict[str, float]]) -> str:
    parts = [
        f"count={count}",
        f"avg_ms={_mean(totals):.2f}",
        f"p95_ms={_percentile(totals, 95):.2f}",
        f"max_ms={max(totals):.2f}",
    ]
    for step in _timing_steps(timing_samples):
        values = [sample[step] for sample in timing_samples if step in sample]
        parts.append(f"{step}_avg={_mean(values):.2f}")
        parts.append(f"{step}_max={max(values):.2f}")
    return " ".join(parts)


def benchmark_failures(
    totals: list[float],
    timing_samples: list[dict[str, float]],
    max_avg_ms: float | None = None,
    max_ms: float | None = None,
    max_step_ms: dict[str, float] | None = None,
) -> list[str]:
    failures: list[str] = []
    if max_avg_ms is not None and _mean(totals) > max_avg_ms:
        failures.append(f"avg_ms {_mean(totals):.2f} exceeds {max_avg_ms:.2f}")
    if max_ms is not None and max(totals) > max_ms:
        failures.append(f"max_ms {max(totals):.2f} exceeds {max_ms:.2f}")
    for step, threshold in (max_step_ms or {}).items():
        values = [sample[step] for sample in timing_samples if step in sample]
        if values and max(values) > threshold:
            failures.append(f"{step}_max {max(values):.2f} exceeds {threshold:.2f}")
    return failures


def parse_step_thresholds(raw_values: list[str] | None) -> dict[str, float]:
    thresholds: dict[str, float] = {}
    for raw in raw_values or []:
        if "=" not in raw:
            raise ValueError(f"step threshold must be STEP=MS: {raw}")
        step, value = raw.split("=", 1)
        step = step.strip()
        if not step:
            raise ValueError(f"step threshold step is empty: {raw}")
        threshold = float(value)
        if threshold < 0:
            raise ValueError(f"step threshold must be non-negative: {raw}")
        thresholds[step] = threshold
    return thresholds


def _timing_steps(timing_samples: list[dict[str, float]]) -> list[str]:
    steps = sorted({step for sample in timing_samples for step in sample if step != "total_ms"})
    if any("total_ms" in sample for sample in timing_samples):
        steps.append("total_ms")
    return steps


def _mean(values: list[float]) -> float:
    return sum(values) / max(len(values), 1)


def _percentile(values: list[float], percentile: int) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile / 100)
    return ordered[index]


def _join_summary(values: list[str]) -> str:
    return _quote(" | ".join(values))


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
