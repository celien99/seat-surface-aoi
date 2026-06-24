from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from python_detector.config.recipe_schema import Recipe, load_recipe_file
from python_detector.ipc.shm_protocol import SHM_PROTOCOL_VERSION, assert_protocol_layout, protocol_sizes
from tools.validate_model_assets import validate_recipe_model_assets


ReadinessScope = Literal["reference", "production"]
ReadinessStatus = Literal["OK", "WARN", "BLOCKED"]

REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ReadinessItem:
    status: ReadinessStatus
    area: str
    requirement: str
    evidence: str
    next_step: str = ""


def validate_architecture_readiness(scope: ReadinessScope = "reference") -> list[ReadinessItem]:
    """按 V4/PPT 架构要求静态检查当前仓库就绪度。

    reference 范围用于验证参考实现是否具备完整工程链路；production 范围会把
    真实生产配置、真实模型资产等上线条件作为阻塞项检查。
    """

    if scope not in ("reference", "production"):
        raise ValueError(f"scope 必须是 reference 或 production: {scope}")

    items: list[ReadinessItem] = []
    items.extend(_check_protocol())
    items.extend(_check_runtime_configs(scope))
    items.extend(_check_python_recipes())
    items.extend(_check_v4_algorithm_contract(scope))
    items.extend(_check_trace_training_and_ops(scope))
    return items


def summarize_readiness(items: list[ReadinessItem]) -> dict[str, int]:
    summary = {"OK": 0, "WARN": 0, "BLOCKED": 0}
    for item in items:
        summary[item.status] += 1
    return summary


def readiness_to_dict(items: list[ReadinessItem]) -> dict[str, Any]:
    return {
        "summary": summarize_readiness(items),
        "items": [
            {
                "status": item.status,
                "area": item.area,
                "requirement": item.requirement,
                "evidence": item.evidence,
                "next_step": item.next_step,
            }
            for item in items
        ],
    }


def _check_protocol() -> list[ReadinessItem]:
    try:
        assert_protocol_layout()
    except AssertionError as exc:
        return [
            _blocked(
                "共享内存协议",
                "C++/Python 在线图像与结果必须使用一致的固定布局共享内存协议。",
                f"协议布局校验失败: {exc}",
                "同步更新 C++/Python 协议结构、校验工具和文档。",
            )
        ]

    sizes = protocol_sizes()
    if SHM_PROTOCOL_VERSION != 2:
        return [
            _blocked(
                "共享内存协议",
                "双方案采集要求协议携带 capture_mode、view/pose、shot 和机器人位姿元数据。",
                f"当前协议版本为 v{SHM_PROTOCOL_VERSION}",
                "使用 v2 协议并保持 C++/Python 布局一致。",
            )
        ]
    return [
        _ok(
            "共享内存协议",
            "协议需支持固定机位视角元数据，并可校验 CRC/布局。",
            "protocol v2; "
            f"LightFrameMeta={sizes['LightFrameMeta']} bytes, "
            f"SeatJobMeta={sizes['SeatJobMeta']} bytes, "
            f"DefectResultMeta={sizes['DefectResultMeta']} bytes",
        )
    ]


def _check_runtime_configs(scope: ReadinessScope) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    fixed_config = _read_key_value_config(REPO_ROOT / "cpp_controller/config/station_runtime.test.conf")

    items.extend(_check_fixed_camera_config(fixed_config))
    items.extend(_check_production_runtime_config(scope))
    return items


def _check_fixed_camera_config(config: dict[str, str]) -> list[ReadinessItem]:
    if config.get("capture_mode") != "fixed_camera":
        return [
            _blocked(
                "固定机位多光源",
                "固定机位方案必须可通过 capture_mode=fixed_camera 独立配置。",
                f"station_runtime.test.conf capture_mode={config.get('capture_mode')}",
                "修正固定机位测试运行配置。",
            )
        ]

    camera_ids = _indexed_values(config, "camera", "camera_id")
    light_order = _split_csv(config.get("light_order", ""))
    missing = _missing_required(["1", "2", "3"], light_order)
    if not camera_ids or missing:
        return [
            _blocked(
                "固定机位多光源",
                "固定机位参考方案必须包含 2 个相机视角和 3 路共享频闪光源。",
                f"camera_ids={camera_ids}, light_order={light_order}",
                "补齐 camera.<N> 与 light_order=1,2,3。",
            )
        ]

    return [
        _ok(
            "固定机位多光源",
            "固定机位方案需按共享光源并行调度完成双相机三光源采集。",
            f"{len(camera_ids)} 个固定视角; light_order={','.join(light_order)}; "
            "触发同步由 trigger_line/exposure_output_line 与频闪接线共同定义",
        )
    ]


def _check_production_runtime_config(scope: ReadinessScope) -> list[ReadinessItem]:
    fixed_deployed_path = REPO_ROOT / "cpp_controller/config/station_runtime.production.conf"
    items: list[ReadinessItem] = []
    deployed_issues = _deployed_fixed_config_issues(fixed_deployed_path)
    if deployed_issues and scope == "production":
        items.append(
            _blocked(
                "生产运行配置",
                "当前固定双机位产线必须生成不含占位值的 station_runtime.production.conf。",
                "; ".join(deployed_issues),
                "按现场 PLC/相机/光源参数生成正式 production.conf 并运行 --validate-config。",
            )
        )
    elif deployed_issues:
        items.append(
            _warn(
                "生产运行配置",
                "当前固定双机位产线放行前必须补齐正式 production.conf。",
                "; ".join(deployed_issues),
                "进入现场联调后生成正式 station_runtime.production.conf。",
            )
        )
    else:
        items.append(
            _ok(
                "生产运行配置",
                "当前固定双机位生产配置已生成。",
                "固定机位 production.conf 存在且未发现占位值。",
            )
        )

    if fixed_deployed_path.exists():
        config = _read_key_value_config(fixed_deployed_path)
        cxx_light_ids = _light_ids_from_order(_split_csv(config.get("light_order", "")))
        recipe = load_recipe_file(REPO_ROOT / "python_detector/config/production_recipe.yaml")
        required_lights = list(recipe.quality.required_lights)
        missing_lights = _missing_required(required_lights, cxx_light_ids)
        if missing_lights:
            status = _blocked if scope == "production" else _warn
            items.append(
                status(
                    "生产光源配方对齐",
                    "固定机位 production.conf 的 light_order 必须覆盖 Python 生产配方 required_lights。",
                    f"C++ light_order={config.get('light_order')} -> {cxx_light_ids}; "
                    f"Python required_lights={required_lights}; missing={missing_lights}",
                    "按当前产线真实光源数量同步 C++ light_order、Python required_lights、模型输入通道和测试。",
                )
            )
        else:
            items.append(
                _ok(
                    "生产光源配方对齐",
                    "固定机位 C++ 采集光源与 Python 生产配方必需光源一致。",
                    f"C++ lights={cxx_light_ids}; Python required_lights={required_lights}",
                )
            )
    return items


def _deployed_fixed_config_issues(path: Path) -> list[str]:
    if not path.exists():
        return [f"缺少 {path.relative_to(REPO_ROOT)}"]
    text = path.read_text(encoding="utf-8")
    if "TODO" in text or "PLACEHOLDER" in text:
        return [f"{path.relative_to(REPO_ROOT)} 仍含 TODO/PLACEHOLDER"]
    config = _read_key_value_config(path)
    issues = []
    if config.get("capture_mode") != "fixed_camera":
        issues.append(f"capture_mode={config.get('capture_mode')}")
    if config.get("light_order") != "1,2,3":
        issues.append(f"当前产线应为 3 路共享频闪光源 light_order=1,2,3，实际={config.get('light_order')}")
    camera_ids = _indexed_values(config, "camera", "camera_id")
    if len(camera_ids) != 2:
        issues.append(f"当前产线应为 2 相机，实际 camera_ids={camera_ids}")
    return issues


def _check_python_recipes() -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    default_recipe = load_recipe_file(REPO_ROOT / "python_detector/config/default_recipe.yaml")
    robot_recipe = load_recipe_file(REPO_ROOT / "python_detector/config/robot_flyshot_recipe.yaml")

    items.extend(_check_recipe_light_quality_trace(default_recipe, "固定机位检测配方"))
    items.extend(_check_recipe_light_quality_trace(robot_recipe, "机器人飞拍检测配方"))

    robot_views = {(camera.camera_id, camera.pose_id) for camera in robot_recipe.cameras}
    shared_camera_ids = {
        camera_id
        for camera_id in {camera.camera_id for camera in robot_recipe.cameras}
        if sum(1 for view_camera_id, _ in robot_views if view_camera_id == camera_id) > 1
    }
    if not shared_camera_ids:
        items.append(
            _blocked(
                "机器人飞拍检测配方",
                "机器人飞拍检测配方需要支持同一末端相机对应多个 pose_id。",
                f"views={sorted(robot_views)}",
                "在 robot_flyshot_recipe.yaml 中为 EYE_IN_HAND 配置多个 pose_id。",
            )
        )
    else:
        items.append(
            _ok(
                "机器人飞拍检测配方",
                "Python 需按 (camera_id, pose_id) 组包，允许末端相机复用。",
                f"shared_camera_ids={sorted(shared_camera_ids)}; views={sorted(robot_views)}",
            )
        )
    return items


def _check_recipe_light_quality_trace(recipe: Recipe, area: str) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    required_semantics = ["DOME", "DARKFIELD_L", "BRIGHTFIELD"]
    semantic_missing = _missing_required(required_semantics, recipe.v4_lights.semantic_to_light_id.keys())
    required_lights = ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"]
    light_missing = _missing_required(required_lights, recipe.quality.required_lights)
    if semantic_missing or light_missing:
        items.append(
            _blocked(
                area,
                "配方必须包含当前三光源生产链路所需 V4 语义光源映射和质量门禁光源。",
                f"missing_semantics={semantic_missing}, missing_required_lights={light_missing}",
                "补齐 DOME/DARKFIELD_L/BRIGHTFIELD 映射和三路 required_lights。",
            )
        )
    else:
        items.append(
            _ok(
                area,
                "配方需声明 Dome、左暗场和 BrightField 的三光源语义映射。",
                f"recipe_id={recipe.recipe_id}; required_lights={list(recipe.quality.required_lights)}",
            )
        )

    if recipe.quality.require_monotonic_timestamps and recipe.quality.require_unique_frame_indices:
        items.append(
            _ok(
                area,
                "质量门禁需覆盖时间戳单调、帧号唯一、曝光/增益漂移和配准误差等不确定状态。",
                "require_monotonic_timestamps=true; require_unique_frame_indices=true; "
                f"max_registration_error_px={recipe.quality.max_registration_error_px}",
            )
        )
    else:
        items.append(
            _blocked(
                area,
                "质量门禁不能允许乱序帧或重复帧输出 OK。",
                "时间戳单调或帧号唯一检查未启用。",
                "启用 require_monotonic_timestamps 与 require_unique_frame_indices。",
            )
        )

    if recipe.trace.enabled and recipe.trace.save_ng and recipe.trace.save_recheck:
        items.append(
            _ok(
                area,
                "NG/RECHECK 需要可追溯，便于模型闭环和现场复盘。",
                f"trace_root={recipe.trace.root_dir}; save_ng=true; save_recheck=true",
            )
        )
    else:
        items.append(
            _blocked(
                area,
                "NG/RECHECK/ERROR 默认应保存 trace。",
                f"trace.enabled={recipe.trace.enabled}, save_ng={recipe.trace.save_ng}, "
                f"save_recheck={recipe.trace.save_recheck}",
                "开启 trace 并保存 NG/RECHECK。",
            )
        )
    return items


def _check_v4_algorithm_contract(scope: ReadinessScope) -> list[ReadinessItem]:
    recipe = load_recipe_file(REPO_ROOT / "python_detector/config/production_recipe.yaml")
    items: list[ReadinessItem] = []
    if recipe.roi_locator.backend in {"onnx_yolo", "onnx_yolo_seg"} and recipe.roi_locator.model_path:
        items.append(
            _ok(
                "V4 ROI 定位",
                "Dome ROI 定位需支持 ONNX YOLO/seg 后端和明确输出解码。",
                f"backend={recipe.roi_locator.backend}; model_path={recipe.roi_locator.model_path}; "
                f"decode={recipe.roi_locator.output_decode}",
            )
        )
    else:
        items.append(
            _blocked(
                "V4 ROI 定位",
                "生产模型配方必须声明 ONNX ROI 定位入口。",
                f"backend={recipe.roi_locator.backend}, model_path={recipe.roi_locator.model_path}",
                "补齐 roi_locator.backend=onnx_yolo 或 onnx_yolo_seg 和 model_path。",
            )
        )

    if recipe.registration.method == "ecc":
        items.append(
            _ok(
                "V4 ROI 配准",
                "多光源 ROI 需要以基准光源执行 ECC 对齐，失败走 RECHECK。",
                f"method=ecc; max_iterations={recipe.registration.max_iterations}; "
                f"min_correlation={recipe.registration.min_correlation}",
            )
        )
    else:
        items.append(
            _warn(
                "V4 ROI 配准",
                "参考配方可用固定标定，生产模型模板应使用 ECC 配准。",
                f"method={recipe.registration.method}",
                "生产配方使用 registration.method=ecc。",
            )
        )

    primary_onnx = [
        key
        for key, model in recipe.models.items()
        if model.role == "primary" and model.backend == "onnx" and model.model_family == "supervised"
    ]
    patchcore = [
        key
        for key, model in recipe.models.items()
        if model.role == "safety_net"
        and model.backend == "patchcore_knn"
        and model.embedding_backend == "onnx_wideresnet50"
        and model.pca_path
        and model.memory_bank_path
        and model.faiss_index_path
    ]
    if primary_onnx and patchcore:
        items.append(
            _ok(
                "V4 AI Runtime",
                "V4.0 双采集模式统一架构要求 ONNX 监督模型 + WideResNet50/PCA/PatchCore/FAISS safety net 接入点。",
                f"primary_onnx={primary_onnx}; patchcore_safety_net={patchcore}",
            )
        )
    else:
        items.append(
            _blocked(
                "V4 AI Runtime",
                "生产模型配方必须同时声明监督 ONNX 与 PatchCore safety net 工程入口。",
                f"primary_onnx={primary_onnx}; patchcore_safety_net={patchcore}",
                "补齐 production_recipe.yaml 的模型后端声明。",
            )
        )

    if set(recipe.quality.required_lights) == {"DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"}:
        items.append(
            _ok(
                "固定机位生产光源证据",
                "当前固定双机位产线采用 3 路共享频闪检测光源，Python 生产配方不得要求额外采集轮次。",
                f"required_lights={list(recipe.quality.required_lights)}; "
                f"model_channels={list(recipe.models['supervised_defect_onnx'].input_channels)}",
            )
        )
    else:
        items.append(
            _blocked(
                "固定机位生产光源证据",
                "当前产线事实为 2 相机 x 3 路共享频闪光源，生产配方必须与真实检测光源一致。",
                f"required_lights={list(recipe.quality.required_lights)}",
                "把 production_recipe.yaml 对齐为 DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT 三光源，或同步现场硬件变更。",
            )
        )

    asset_issues = validate_recipe_model_assets(recipe)
    if asset_issues and scope == "production":
        items.append(
            _blocked(
                "生产模型资产",
                "生产上线必须替换真实 YOLO/ONNX/WideResNet50/PCA/PatchCore/FAISS 资产。",
                _format_asset_issues(asset_issues),
                "替换 model/ 下占位资产并运行 tools.validate_model_assets。",
            )
        )
    elif asset_issues:
        items.append(
            _warn(
                "生产模型资产",
                "参考实现已提供真实模型接入点，但当前仍使用占位产物。",
                _format_asset_issues(asset_issues),
                "现场数据完成训练评估后替换真实模型资产。",
            )
        )
    else:
        items.append(
            _ok(
                "生产模型资产",
                "生产模型资产需存在且元数据版本一致。",
                "tools.validate_model_assets 未发现错误。",
            )
        )
    return items


def _check_trace_training_and_ops(scope: ReadinessScope) -> list[ReadinessItem]:
    items: list[ReadinessItem] = []
    required_paths = [
        "python_detector/trace/trace_writer.py",
        "training_tools/collect_shm_dataset.py",
        "training_tools/collect_trace_dataset.py",
        "training_tools/dataset_manifest.py",
        "training_tools/extract_embeddings.py",
        "training_tools/export_wideresnet_embedding.py",
        "training_tools/evaluate_pipeline.py",
        "training_tools/train_patchcore_assets.py",
        "training_tools/benchmark_pipeline.py",
        "training_tools/build_patchcore_memory_bank.py",
        "training_tools/build_faiss_index.py",
        "training_tools/train_roi_yolo.py",
        "training_tools/train_supervised_yolo.py",
        "docs/python_detector_operations.md",
    ]
    missing = [path for path in required_paths if not (REPO_ROOT / path).exists()]
    if missing:
        items.append(
            _blocked(
                "追溯与训练闭环",
                "V4.0 双采集模式统一架构的数据管理与模型闭环需要 trace、回放、benchmark 和训练样本导出入口。",
                f"缺少: {missing}",
                "补齐 trace/training_tools 工程入口。",
            )
        )
    else:
        items.append(
            _ok(
                "追溯与训练闭环",
                "NG/RECHECK trace 与共享内存多光源任务应能转训练样本，并支持真实 ROI 图 embedding、评估、WideResNet/PatchCore/FAISS 资产训练、YOLO 导出、回放和性能 benchmark。",
                "trace_writer、共享内存采集、manifest、embedding、evaluate、WideResNet、PatchCore/FAISS、YOLO、benchmark 工具均存在。",
            )
        )

    ops_docs = [
        "docs/README.md",
        "docs/cpp_controller_operations.md",
        "docs/python_detector_operations.md",
    ]
    missing_ops_docs = [path for path in ops_docs if not (REPO_ROOT / path).exists()]
    if missing_ops_docs:
        items.append(
            _blocked(
                "部署运维闭环",
                "V4.0 双采集模式统一架构的系统监控、上线验收和异常恢复需要精简后的运维文档。",
                f"缺少: {missing_ops_docs}",
                "补齐 docs 总览、C++ 运维和 Python 算法运维文档。",
            )
        )
    else:
        items.append(
            _ok(
                "部署运维闭环",
                "项目需提供 docs 总览、C++ 运维和 Python 算法运维说明。",
                "精简后的部署与运维文档齐备。",
            )
        )

    items.append(
        _warn(
            "MES/报警/监控平台",
            "V4.0 双采集模式统一架构中的 MES、报警输出面板和系统监控平台属于现场平台集成，不由模拟链路证明。",
            "当前仓库提供 C++ 事件日志、SOP 和接口边界，但没有完整 MES/监控服务实现。",
            "按现场平台协议扩展 PLC/MES/报警/监控适配器，并做端到端验收。"
            if scope == "production"
            else "生产项目阶段再接入现场平台。",
        )
    )
    return items


def _read_key_value_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def _indexed_values(config: dict[str, str], prefix: str, field: str) -> list[str]:
    values = []
    suffix = f".{field}"
    for key, value in sorted(config.items()):
        if key.startswith(f"{prefix}.") and key.endswith(suffix) and value:
            values.append(value)
    return values


def _indexed_records(config: dict[str, str], prefix: str) -> dict[int, dict[str, str]]:
    records: dict[int, dict[str, str]] = {}
    prefix_text = f"{prefix}."
    for key, value in config.items():
        if not key.startswith(prefix_text):
            continue
        rest = key[len(prefix_text) :]
        index_text, separator, field = rest.partition(".")
        if not separator:
            continue
        try:
            index = int(index_text)
        except ValueError:
            continue
        records.setdefault(index, {})[field] = value
    return dict(sorted(records.items()))


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _light_ids_from_order(light_order: list[str]) -> list[str]:
    light_id_by_index = {
        "1": "DIFFUSE",
        "2": "POLAR_DIFFUSE",
        "3": "HIGH_LEFT",
        "4": "HIGH_RIGHT",
        "5": "HIGH_FRONT",
        "6": "HIGH_REAR",
        "7": "LOW_LEFT",
        "8": "LOW_RIGHT",
        "9": "LOW_FRONT",
        "10": "LOW_REAR",
        "11": "NIR",
        "12": "DOME_ROI",
    }
    return [light_id_by_index.get(index, f"LIGHT_{index}") for index in light_order]


def _missing_required(required: list[str], present: Any) -> list[str]:
    present_set = set(present)
    return [item for item in required if item not in present_set]


def _format_asset_issues(issues: list[Any]) -> str:
    messages = [f"{issue.location}: {issue.message}" for issue in issues[:4]]
    if len(issues) > 4:
        messages.append(f"... 另有 {len(issues) - 4} 项")
    return "; ".join(messages)


def _ok(area: str, requirement: str, evidence: str, next_step: str = "") -> ReadinessItem:
    return ReadinessItem("OK", area, requirement, evidence, next_step)


def _warn(area: str, requirement: str, evidence: str, next_step: str = "") -> ReadinessItem:
    return ReadinessItem("WARN", area, requirement, evidence, next_step)


def _blocked(area: str, requirement: str, evidence: str, next_step: str = "") -> ReadinessItem:
    return ReadinessItem("BLOCKED", area, requirement, evidence, next_step)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按 V4/PPT 架构要求检查当前项目就绪度")
    parser.add_argument(
        "--scope",
        choices=("reference", "production"),
        default="reference",
        help="reference 检查参考实现闭环；production 检查上线阻塞项",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整检查结果")
    args = parser.parse_args(argv)

    items = validate_architecture_readiness(args.scope)
    payload = readiness_to_dict(items)
    if args.json:
        print(json.dumps({"scope": args.scope, **payload}, ensure_ascii=False, indent=2))
    else:
        summary = payload["summary"]
        print(
            f"架构就绪度检查: scope={args.scope} "
            f"OK={summary['OK']} WARN={summary['WARN']} BLOCKED={summary['BLOCKED']}"
        )
        for item in items:
            print(f"[{item.status}] {item.area}: {item.requirement}")
            print(f"  evidence: {item.evidence}")
            if item.next_step:
                print(f"  next: {item.next_step}")
    return 1 if payload["summary"]["BLOCKED"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
