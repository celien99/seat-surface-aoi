from __future__ import annotations

from pathlib import Path

from tools.validate_deployment_preflight import (
    preflight_to_dict,
    validate_deployment_preflight,
)


def test_deployment_preflight_handoff_has_no_local_blockers() -> None:
    items = validate_deployment_preflight()
    payload = preflight_to_dict(items)

    assert payload["summary"]["BLOCKED"] == 0
    assert "生产运行配置" in payload["field_actions"]
    assert "生产光源配方对齐" in payload["field_actions"]
    assert "生产模型资产" in payload["field_actions"]
    assert any(item.category == "Windows 共享内存映射" and item.status == "OK" for item in items)
    assert any(item.category == "跨平台模拟 IPC 入口" and item.status == "OK" for item in items)
    assert any(item.category == "部署包交接入口" and item.status == "OK" for item in items)


def test_deployment_preflight_strict_blocks_field_assets_and_configs() -> None:
    items = validate_deployment_preflight(strict_production=True)
    blocked = {item.category for item in items if item.status == "BLOCKED"}

    assert "生产运行配置" in blocked
    assert "生产光源配方对齐" in blocked
    assert "生产模型资产" in blocked
    assert all(not item.local_actionable for item in items if item.category in blocked)


def test_package_script_includes_deployment_preflight_tool() -> None:
    text = Path("tools/package_release.sh").read_text(encoding="utf-8")

    assert "validate_deployment_preflight.py" in text
    assert "tools.validate_deployment_preflight" in text
