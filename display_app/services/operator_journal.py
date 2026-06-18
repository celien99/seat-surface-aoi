from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


class OperatorJournal:
    """Persist display-side logs, review queue, and operator actions."""

    def __init__(self, trace_root: str | Path) -> None:
        self.trace_root = Path(trace_root)
        self.events_path = self.trace_root / "display_operator_events.jsonl"
        self.review_path = self.trace_root / "display_review_queue.json"

    def load_logs(self, *, limit: int = 500) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if not self.events_path.exists():
            return rows
        try:
            for line in self.events_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                payload = json.loads(line)
                if isinstance(payload, dict) and payload.get("record_type") == "log":
                    row = payload.get("record")
                    if isinstance(row, dict):
                        rows.append(row)
        except (OSError, json.JSONDecodeError):
            return []
        return rows[-limit:]

    def append_log(self, record: dict[str, Any]) -> None:
        self._append_event({"record_type": "log", "record": record})

    def append_action(self, record: dict[str, Any]) -> None:
        self._append_event({"record_type": "operator_action", "record": record})

    def load_reviews(self) -> list[dict[str, Any]]:
        if not self.review_path.exists():
            return []
        try:
            payload = json.loads(self.review_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(payload, list):
            return []
        return [item for item in payload if isinstance(item, dict)]

    def save_reviews(self, reviews: list[dict[str, Any]]) -> None:
        self.trace_root.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(reviews, ensure_ascii=False, indent=2)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{self.review_path.name}.",
            suffix=".tmp",
            dir=str(self.trace_root),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                output.write(payload)
                output.write("\n")
            Path(tmp_name).replace(self.review_path)
        finally:
            tmp_path = Path(tmp_name)
            if tmp_path.exists():
                tmp_path.unlink()

    def _append_event(self, payload: dict[str, Any]) -> None:
        self.trace_root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as output:
            output.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            output.write("\n")
