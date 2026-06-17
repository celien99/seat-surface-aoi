from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tools.validate_architecture_readiness import readiness_to_dict, validate_architecture_readiness
from tools.validate_model_assets import load_recipe_by_id_or_path, validate_recipe_model_assets


PreflightStatus = Literal["OK", "ACTION", "BLOCKED"]

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_RECIPES = (
    "seat_a_black_leather_production_v1",
    "seat_a_robot_flyshot_production_v1",
)


@dataclass(frozen=True)
class PreflightItem:
    status: PreflightStatus
    category: str
    requirement: str
    evidence: str
    owner: str
    next_step: str
    local_actionable: bool = False


def validate_deployment_preflight(*, strict_production: bool = False) -> list[PreflightItem]:
    """汇总上 Windows 工控机前的本地预检和现场交接项。

    默认模式只把当前仓库可补齐、可验证的工程缺口作为 BLOCKED；真实硬件参数、
    真实模型资产和现场平台协议以 ACTION 输出。strict_production 用于上机放行前
    的最后门禁，会把正式生产配置和真实模型资产缺失升级为 BLOCKED。
    """

    items: list[PreflightItem] = []
    items.extend(_check_reference_architecture())
    items.extend(_check_windows_shared_memory_mapping())
    items.extend(_check_cross_platform_ipc_entry())
    items.extend(_check_package_handoff_entry())
    items.extend(_check_lab_manual_entry())
    items.extend(_check_production_runtime_configs(strict_production))
    items.extend(_check_production_model_assets(strict_production))
    items.append(
        PreflightItem(
            status="ACTION",
            category="现场平台接口",
            requirement="MES、报警面板和生产监控需要按现场平台协议完成适配。",
            evidence="仓库已提供 C++ 事件日志、PLC 输出边界和运维 SOP，但没有现场 MES/监控服务。",
            owner="现场平台/产线集成",
            next_step="明确 MES/报警/监控协议后，在 C++ 输出或外部平台层完成适配并做端到端验收。",
        )
    )
    return items


def summarize_preflight(items: list[PreflightItem]) -> dict[str, int]:
    summary = {"OK": 0, "ACTION": 0, "BLOCKED": 0}
    for item in items:
        summary[item.status] += 1
    return summary


def preflight_to_dict(items: list[PreflightItem]) -> dict[str, Any]:
    return {
        "summary": summarize_preflight(items),
        "local_blockers": [
            item.category for item in items if item.status == "BLOCKED" and item.local_actionable
        ],
        "field_actions": [
            item.category for item in items if item.status in {"ACTION", "BLOCKED"} and not item.local_actionable
        ],
        "items": [
            {
                "status": item.status,
                "category": item.category,
                "requirement": item.requirement,
                "evidence": item.evidence,
                "owner": item.owner,
                "next_step": item.next_step,
                "local_actionable": item.local_actionable,
            }
            for item in items
        ],
    }


def _check_reference_architecture() -> list[PreflightItem]:
    readiness = validate_architecture_readiness("reference")
    payload = readiness_to_dict(readiness)
    blocked = [item for item in readiness if item.status == "BLOCKED"]
    if blocked:
        return [
            PreflightItem(
                status="BLOCKED",
                category="本地参考链路",
                requirement="当前环境必须先通过 reference 架构检查，才能认为只剩硬件和模型现场项。",
                evidence=_format_readiness_items(blocked),
                owner="本仓库工程实现",
                next_step="修复 reference 检查中的 BLOCKED 项后重新运行本预检。",
                local_actionable=True,
            )
        ]
    summary = payload["summary"]
    return [
        PreflightItem(
            status="OK",
            category="本地参考链路",
            requirement="固定机位、机器人飞拍、共享内存、检测链路和训练闭环在本地参考范围内无阻塞。",
            evidence=f"reference readiness OK={summary['OK']} WARN={summary['WARN']} BLOCKED={summary['BLOCKED']}",
            owner="本仓库工程实现",
            next_step="继续保持 uv run pytest、tools.validate_protocol 和模拟 IPC 作为默认回归。",
        )
    ]


def _check_windows_shared_memory_mapping() -> list[PreflightItem]:
    py_path = REPO_ROOT / "python_detector/ipc/shared_memory_map.py"
    cpp_path = REPO_ROOT / "cpp_controller/src/ipc/shared_memory_win32.cpp"
    py_text = _read_text(py_path)
    cpp_text = _read_text(cpp_path)
    missing = []
    if "Local\\\\" not in py_text or "shared_memory.SharedMemory" not in py_text:
        missing.append(str(py_path.relative_to(REPO_ROOT)))
    if "CreateFileMappingW" not in cpp_text or "OpenFileMappingW" not in cpp_text or "Local\\\\" not in cpp_text:
        missing.append(str(cpp_path.relative_to(REPO_ROOT)))
    if missing:
        return [
            PreflightItem(
                status="BLOCKED",
                category="Windows 共享内存映射",
                requirement="Windows 工控机必须使用 Named Shared Memory，且逻辑名映射到 Local\\ 命名空间。",
                evidence=f"映射入口缺失或不完整: {missing}",
                owner="本仓库工程实现",
                next_step="补齐 C++ Win32 和 Python 共享内存名称映射后再上机。",
                local_actionable=True,
            )
        ]
    return [
        PreflightItem(
            status="OK",
            category="Windows 共享内存映射",
            requirement="C++ 与 Python 在 Windows 上使用一致的 Named Shared Memory 逻辑名映射。",
            evidence="C++ 使用 CreateFileMappingW/OpenFileMappingW；Python 使用 multiprocessing.shared_memory；均映射 Local\\。",
            owner="本仓库工程实现",
            next_step="在 Windows 工控机用 uv run python tools/run_simulated_ipc.py 做首次共享内存烟测。",
        )
    ]


def _check_cross_platform_ipc_entry() -> list[PreflightItem]:
    py_entry = REPO_ROOT / "tools/run_simulated_ipc.py"
    sh_entry = REPO_ROOT / "tools/run_simulated_ipc.sh"
    missing = [str(path.relative_to(REPO_ROOT)) for path in (py_entry, sh_entry) if not path.exists()]
    py_text = _read_text(py_entry)
    if "--config" not in py_text or "EXE_SUFFIX" not in py_text or "detector_main" not in py_text:
        missing.append(str(py_entry.relative_to(REPO_ROOT)))
    if missing:
        return [
            PreflightItem(
                status="BLOCKED",
                category="跨平台模拟 IPC 入口",
                requirement="工控机上机前必须有不依赖 bash 的 Python 模拟 IPC 入口，并能同步 C++/Python 配置。",
                evidence=f"入口缺失或参数不完整: {missing}",
                owner="本仓库工程实现",
                next_step="修复 tools/run_simulated_ipc.py 后重新运行模拟 IPC。",
                local_actionable=True,
            )
        ]
    return [
        PreflightItem(
            status="OK",
            category="跨平台模拟 IPC 入口",
            requirement="Windows 工控机可用 Python 入口运行 C++/Python 端到端共享内存烟测。",
            evidence="tools/run_simulated_ipc.py 支持 .exe 构建产物、--config 同步和 detector_main 启动。",
            owner="本仓库工程实现",
            next_step="上机后先运行 uv run python tools/run_simulated_ipc.py，再接真实硬件 backend。",
        )
    ]


def _check_package_handoff_entry() -> list[PreflightItem]:
    package_script = REPO_ROOT / "tools/package_release.sh"
    text = _read_text(package_script)
    required_tokens = [
        "validate_package.sh",
        "run_packaged_simulated_ipc.sh",
        "validate_architecture_readiness.py",
        "validate_deployment_preflight.py",
        "run_simulated_ipc.py",
    ]
    missing = [token for token in required_tokens if token not in text]
    if missing:
        return [
            PreflightItem(
                status="BLOCKED",
                category="部署包交接入口",
                requirement="离线部署包必须包含基础校验、模拟 IPC 和上机预检入口。",
                evidence=f"package_release.sh 未覆盖: {missing}",
                owner="本仓库工程实现",
                next_step="更新打包脚本，确保工控机解包后可以直接做协议、IPC 和预检。",
                local_actionable=True,
            )
        ]
    return [
        PreflightItem(
            status="OK",
            category="部署包交接入口",
            requirement="离线部署包带有协议校验、IPC 诊断、模拟 IPC 和上机预检。",
            evidence="package_release.sh 会写入 validate_package.sh、run_packaged_simulated_ipc.sh 并复制预检工具。",
            owner="本仓库工程实现",
            next_step="打包后在目标机先运行 bash validate_package.sh 和预检命令。",
        )
    ]


def _check_lab_manual_entry() -> list[PreflightItem]:
    path = REPO_ROOT / "cpp_controller/config/station_runtime.lab_manual.example.conf"
    config = _read_key_value_config(path)
    if config.get("hardware_mode") != "lab" or config.get("plc.backend") != "manual_trigger":
        return [
            PreflightItem(
                status="BLOCKED",
                category="PLC 前手动联调路径",
                requirement="PLC 接入前需要 lab/manual_trigger 配置验证真实相机、频闪和共享内存收图。",
                evidence=f"hardware_mode={config.get('hardware_mode')}, plc.backend={config.get('plc.backend')}",
                owner="本仓库工程实现",
                next_step="修复 station_runtime.lab_manual.example.conf。",
                local_actionable=True,
            )
        ]
    return [
        PreflightItem(
            status="OK",
            category="PLC 前手动联调路径",
            requirement="PLC 未接入时可以先用手动触发联调真实相机、频闪和 Python 收图。",
            evidence="station_runtime.lab_manual.example.conf 使用 hardware_mode=lab 与 plc.backend=manual_trigger。",
            owner="本仓库工程实现",
            next_step="上机后复制为 station_runtime.lab_manual.conf，替换相机序列号和频闪接线参数。",
        )
    ]


def _check_production_runtime_configs(strict_production: bool) -> list[PreflightItem]:
    expected = [
        REPO_ROOT / "cpp_controller/config/station_runtime.production.conf",
        REPO_ROOT / "cpp_controller/config/station_runtime.robot_flyshot.production.conf",
    ]
    existing = [path for path in expected if path.exists()]
    missing = [str(path.relative_to(REPO_ROOT)) for path in expected if not path.exists()]
    todo_counts = {
        str(path.relative_to(REPO_ROOT)): _count_placeholder_tokens(path)
        for path in existing
        if _count_placeholder_tokens(path) > 0
    }
    if missing or todo_counts:
        status: PreflightStatus = "BLOCKED" if strict_production else "ACTION"
        return [
            PreflightItem(
                status=status,
                category="生产运行配置",
                requirement="正式上产线前必须生成不含 TODO/占位值的 production.conf，并通过 C++ --validate-config。",
                evidence=f"missing={missing}; placeholder_counts={todo_counts}",
                owner="硬件/电气/现场集成",
                next_step="从 *.production.example.conf 复制正式配置，填写 PLC、相机、频闪、机器人参数并运行 --validate-config。",
            )
        ]
    return [
        PreflightItem(
            status="OK",
            category="生产运行配置",
            requirement="固定机位和机器人飞拍生产配置已生成且未发现占位值。",
            evidence=f"configs={[str(path.relative_to(REPO_ROOT)) for path in expected]}",
            owner="硬件/电气/现场集成",
            next_step="继续用 C++ --validate-config 和现场硬件低速节拍验证配置。",
        )
    ]


def _check_production_model_assets(strict_production: bool) -> list[PreflightItem]:
    issues_by_recipe: dict[str, int] = {}
    examples: list[str] = []
    for recipe_id in PRODUCTION_RECIPES:
        recipe = load_recipe_by_id_or_path(recipe_id)
        issues = validate_recipe_model_assets(recipe)
        if issues:
            issues_by_recipe[recipe_id] = len(issues)
            examples.extend(f"{recipe_id}:{issue.location}={issue.message}" for issue in issues[:2])
    if issues_by_recipe:
        status: PreflightStatus = "BLOCKED" if strict_production else "ACTION"
        return [
            PreflightItem(
                status=status,
                category="生产模型资产",
                requirement="真实产线必须替换 ROI YOLO、监督检测、WideResNet50、PCA、PatchCore 和 FAISS 资产。",
                evidence=f"issue_counts={issues_by_recipe}; examples={examples[:4]}",
                owner="算法/数据/模型",
                next_step="用现场 trace 和人工标注训练资产，替换 model/ 下占位文件并运行 tools.validate_model_assets。",
            )
        ]
    return [
        PreflightItem(
            status="OK",
            category="生产模型资产",
            requirement="生产配方引用的模型资产存在且元数据校验通过。",
            evidence=f"recipes={list(PRODUCTION_RECIPES)}",
            owner="算法/数据/模型",
            next_step="上机后继续按 ROI、材质、颜色、采集模式分层验证 recall/false reject。",
        )
    ]


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _read_key_value_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in _read_text(path).splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _count_placeholder_tokens(path: Path) -> int:
    text = _read_text(path)
    return text.count("TODO") + text.count("PLACEHOLDER")


def _format_readiness_items(items: list[Any]) -> str:
    return "; ".join(f"{item.area}: {item.evidence}" for item in items[:4])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上 Windows 工控机前的部署预检和现场交接清单")
    parser.add_argument(
        "--strict-production",
        action="store_true",
        help="把正式生产配置和真实模型资产缺失作为 BLOCKED，适合上机放行前使用",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整预检结果")
    args = parser.parse_args(argv)

    items = validate_deployment_preflight(strict_production=args.strict_production)
    payload = preflight_to_dict(items)
    if args.json:
        print(json.dumps({"strict_production": args.strict_production, **payload}, ensure_ascii=False, indent=2))
    else:
        summary = payload["summary"]
        mode = "strict-production" if args.strict_production else "handoff"
        print(
            f"部署上机预检: mode={mode} "
            f"OK={summary['OK']} ACTION={summary['ACTION']} BLOCKED={summary['BLOCKED']}"
        )
        for item in items:
            print(f"[{item.status}] {item.category}: {item.requirement}")
            print(f"  evidence: {item.evidence}")
            print(f"  owner: {item.owner}")
            print(f"  next: {item.next_step}")
    return 1 if payload["summary"]["BLOCKED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
