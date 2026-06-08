from __future__ import annotations

import argparse
import statistics

from python_detector.config.recipe_schema import RecipeManager
from python_detector.pipeline.pipeline import InspectionPipeline
from tools.job_fixture import make_simulated_job


def main() -> int:
    parser = argparse.ArgumentParser(description="统计 Python 检测流水线耗时")
    parser.add_argument("--count", type=int, default=10)
    args = parser.parse_args()

    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    totals: list[float] = []
    for index in range(args.count):
        result = pipeline.process(make_simulated_job(index + 1), recipe)
        totals.append(result.elapsed_ms)
    print(f"count={args.count} avg_ms={statistics.mean(totals):.2f} max_ms={max(totals):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

