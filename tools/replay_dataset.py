from __future__ import annotations

import argparse

from python_detector.config.recipe_schema import RecipeManager
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.trace.trace_writer import TraceWriter
from tools.job_fixture import make_simulated_job
from tools.pipeline_report import format_replay_line


def main() -> int:
    parser = argparse.ArgumentParser(description="回放模拟 SeatInspectionJob 并输出检测结果")
    parser.add_argument("--count", type=_positive_int, default=1)
    parser.add_argument("--write-trace", action="store_true")
    parser.add_argument("--summary-limit", type=int, default=5, help="单条结果最多输出的质量失败原因数量")
    args = parser.parse_args()

    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    writer = TraceWriter(recipe.trace.root_dir)
    for index in range(args.count):
        job = make_simulated_job(index + 1)
        result = pipeline.process(job, recipe)
        trace_dir = None
        if args.write_trace:
            trace_dir = writer.write(job, recipe, result, pipeline.last_context)
        print(format_replay_line(result, pipeline.last_context, trace_dir, args.summary_limit))
    return 0


def _positive_int(value: str) -> int:
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("必须大于 0")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
