from __future__ import annotations

from tools.validate_architecture_readiness import (
    readiness_to_dict,
    validate_architecture_readiness,
)


def test_reference_architecture_readiness_has_no_blocked_items() -> None:
    items = validate_architecture_readiness("reference")
    summary = readiness_to_dict(items)["summary"]

    assert summary["BLOCKED"] == 0
    assert summary["OK"] >= 10
    assert any(item.area == "固定机位多光源" and item.status == "OK" for item in items)
    assert any(item.area == "机器人飞拍多光源" and item.status == "OK" for item in items)
    assert any(item.area == "追溯与训练闭环" and item.status == "OK" for item in items)
    assert any(item.area == "生产模型资产" and item.status == "WARN" for item in items)


def test_production_architecture_readiness_blocks_placeholder_assets_and_configs() -> None:
    items = validate_architecture_readiness("production")
    blocked_areas = {item.area for item in items if item.status == "BLOCKED"}

    assert "生产运行配置" in blocked_areas
    assert "生产模型资产" in blocked_areas
