from __future__ import annotations

import importlib
from pathlib import Path


def test_trace_root_resolves_relative_to_repo() -> None:
    tool = importlib.import_module("tools.run_cpp_soak")

    resolved = tool._resolve_path("trace/cpp_soak")

    assert resolved == (tool.ROOT_DIR / "trace" / "cpp_soak").resolve()
    assert tool._is_under_workspace(resolved)


def test_trace_root_rejects_workspace_parent() -> None:
    tool = importlib.import_module("tools.run_cpp_soak")

    assert not tool._is_under_workspace(Path(tool.ROOT_DIR).parent.resolve())
