from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import InspectionResult, SeatInspectionJob


class TraceWriter:
    def __init__(self, root_dir: str | Path = "trace") -> None:
        self.root_dir = Path(root_dir)

    def write(
        self,
        job: SeatInspectionJob,
        recipe: Recipe,
        result: InspectionResult,
        context: dict[str, Any],
    ) -> Path | None:
        if not recipe.trace.enabled:
            return None
        if result.decision == "OK" and recipe.trace.save_ok_ratio <= 0:
            return None
        if result.decision == "NG" and not recipe.trace.save_ng:
            return None
        if result.decision in {"RECHECK", "ERROR"} and not recipe.trace.save_recheck:
            return None

        day = datetime.now().strftime("%Y%m%d")
        safe_seat_id = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in job.seat_id)
        trace_dir = self.root_dir / day / f"{safe_seat_id}_{job.sequence_id}"
        trace_dir.mkdir(parents=True, exist_ok=True)

        self._write_json(trace_dir / "job.json", job)
        self._write_json(trace_dir / "result.json", result)
        self._write_json(trace_dir / "recipe_summary.json", {"recipe_id": recipe.recipe_id, "sku": recipe.sku})
        self._write_json(trace_dir / "quality_report.json", context.get("quality_report"))
        self._write_json(trace_dir / "registration_report.json", context.get("registration_reports", []))
        self._write_json(trace_dir / "feature_summary.json", context.get("feature_summary", []))
        self._write_json(trace_dir / "timings.json", context.get("timings", {}))
        return trace_dir

    def _write_json(self, path: Path, value: Any) -> None:
        path.write_text(json.dumps(_jsonable(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, memoryview):
        return {"memoryview_bytes": len(value)}
    if hasattr(value, "value"):
        return value.value
    return value
