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
    assert "现场平台接口" in payload["field_actions"]
    assert any(item.category == "生产运行配置" and item.status == "OK" for item in items)
    assert any(item.category == "生产光源配方对齐" and item.status == "OK" for item in items)
    assert any(item.category == "生产模型资产" and item.status == "OK" for item in items)
    assert any(item.category == "Windows 共享内存映射" and item.status == "OK" for item in items)
    assert any(item.category == "跨平台模拟 IPC 入口" and item.status == "OK" for item in items)
    assert any(item.category == "部署包交接入口" and item.status == "OK" for item in items)
    assert any(item.category == "Python 离线依赖包" and item.status == "OK" for item in items)


def test_deployment_preflight_strict_has_no_model_asset_blocker_now() -> None:
    items = validate_deployment_preflight(strict_production=True)
    blocked = {item.category for item in items if item.status == "BLOCKED"}

    assert "生产运行配置" not in blocked
    assert "生产光源配方对齐" not in blocked
    assert "生产模型资产" not in blocked
    assert any(item.category == "生产模型资产" and item.status == "OK" for item in items)
    assert all(not item.local_actionable for item in items if item.category in blocked)


def test_package_script_includes_deployment_preflight_tool() -> None:
    text = Path("tools/package_release.py").read_text(encoding="utf-8")

    assert "validate_deployment_preflight.py" in text
    assert "tools.validate_deployment_preflight" in text
    assert "package_python_offline_deps.py" in text
    assert "validate_package.py" in text
    assert "run_packaged_simulated_ipc.py" in text


def test_python_offline_dependency_packager_has_target_installers() -> None:
    text = Path("tools/package_python_offline_deps.py").read_text(encoding="utf-8")

    assert "wheelhouse" in text
    assert "install_offline.ps1" in text
    assert "install_offline." + "sh" not in text
    assert "--no-index" in text
    assert "OFFLINE_DEPS_MANIFEST.json" in text
