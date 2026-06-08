from __future__ import annotations

import argparse

from python_detector.config.recipe_schema import RecipeManager
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter
from tools.job_fixture import make_simulated_job


def main() -> int:
    parser = argparse.ArgumentParser(description="回放模拟 SeatInspectionJob 并输出检测结果")
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--write-trace", action="store_true")
    args = parser.parse_args()

    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    writer = TraceWriter(recipe.trace.root_dir)
    for index in range(args.count):
        job = make_simulated_job(index + 1)
        result = pipeline.process(job, recipe)
        if args.write_trace:
            writer.write(job, recipe, result, pipeline.last_context)
        print(f"sequence_id={result.sequence_id} decision={result.decision} elapsed_ms={result.elapsed_ms:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

