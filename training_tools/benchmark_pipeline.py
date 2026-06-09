from __future__ import annotations

import argparse

from python_detector.config.recipe_schema import RecipeManager
from python_detector.pipeline.pipeline import InspectionPipeline
from training_tools.job_fixture import make_simulated_job
from training_tools.pipeline_report import (
    benchmark_failures,
    collect_timing_samples,
    format_benchmark_report,
    parse_step_thresholds,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 Python 检测流水线耗时")
    parser.add_argument("--count", type=_positive_int, default=10)
    parser.add_argument("--max-avg-ms", type=float, default=None, help="平均总耗时超过该阈值时返回失败")
    parser.add_argument("--max-ms", type=float, default=None, help="单次最大总耗时超过该阈值时返回失败")
    parser.add_argument(
        "--max-step-ms",
        action="append",
        default=[],
        metavar="STEP=MS",
        help="单步骤最大耗时阈值，例如 quality_ms=5，可重复传入",
    )
    args = parser.parse_args()

    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    totals: list[float] = []
    timing_samples: list[dict[str, float]] = []
    for index in range(args.count):
        result = pipeline.process(make_simulated_job(index + 1), recipe)
        totals.append(result.elapsed_ms)
        collect_timing_samples(timing_samples, pipeline.last_context)
    print(format_benchmark_report(args.count, totals, timing_samples))
    failures = benchmark_failures(
        totals,
        timing_samples,
        max_avg_ms=args.max_avg_ms,
        max_ms=args.max_ms,
        max_step_ms=parse_step_thresholds(args.max_step_ms),
    )
    for failure in failures:
        print(f"benchmark_failed={failure}")
    if failures:
        return 2
    return 0


def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return result


if __name__ == "__main__":
    raise SystemExit(main())

