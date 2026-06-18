# 文档总览

`docs/` 已从分散专题文档收敛为少量长期维护入口。当前项目的在线主链路以 V4.0 双采集模式统一架构为准：固定机位多光源和机器人飞拍多光源在 C++ Capture Plan 层汇合，统一通过共享内存进入 Python ONNX + FAISS 检测链路。早期 V2 设计、重复的硬件说明、分散的 Python 算法说明和测试机清单已合并到下列文档中。

## 保留文档

| 文档 | 作用 |
| --- | --- |
| [V4.0 双采集模式架构对齐说明](v4_architecture_alignment.md) | 当前目标架构、已实现能力、生产差距和补齐顺序。 |
| [共享内存协议](shm_protocol.md) | C++ 与 Python 在线 IPC 的固定布局、状态机、错误码和校验要求。 |
| [项目调用关系摘要](project_function_call_map.md) | 端到端调用链、关键模块边界和阅读顺序。 |
| [C++ 主控部署与硬件运维](cpp_controller_operations.md) | PLC、相机、频闪、机器人、生产配置、上线 SOP、测试机联调和失败场景。 |
| [Python 检测算法与模型运维](python_detector_operations.md) | Python 算法入口、配方、ROI/标定、模型后端、trace、训练样本、回放、benchmark 和上机预检。 |
| [工控机上线补齐报告](deployment_readiness_report.md) | 当前固定双机位 + 三光源工控机交接状态、接线、待确认项和启动流程。 |

辅助图片保留在 `docs/assets/`：

- `architecture-v4.png`：V4.0 双采集模式统一架构图。
- `project-function-code-map.png`：项目功能与代码映射图。

## 文档归并结论

| 原文档 | 处理 | 原因与内容去向 |
| --- | --- | --- |
| `seat-defect-inspection-architecture.md` | 删除 | 早期 V2 长文，当前验收以 V4.0 为准；仍有效的 C++/Python/IPC 边界已进入 README、`v4_architecture_alignment.md` 和 `shm_protocol.md`。 |
| `cpp_controller_hardware_manual.md` | 合并 | 与配置 quickstart、SOP、硬件对接大量重复；关键时序、参数、接口、失败场景进入 `cpp_controller_operations.md`。 |
| `cpp_controller_production_config_quickstart.md` | 合并 | 配置字段和上线检查进入 `cpp_controller_operations.md`。 |
| `cpp_controller_production_sop.md` | 合并 | 配置验收、模拟链路、健康报警、长稳压测和放行规则进入 `cpp_controller_operations.md`。 |
| `hardware_integration.md` | 合并 | C++/Python 设备边界和联调顺序进入 `cpp_controller_operations.md`。 |
| `deployment.md` | 合并 | 部署前模型、硬件、模拟 IPC 校验进入 C++/Python 两份运维文档。 |
| `test_machine_integration.md` | 合并 | 测试机上机前检查、联调顺序和失败测试进入 `cpp_controller_operations.md`。 |
| `python_detector_module.md` | 合并 | Python 算法模块边界、公开入口、依赖分组和验证命令进入 `python_detector_operations.md`。 |
| `recipe_design.md` | 合并 | 配方字段、V4 光源映射、质量门禁、阈值安全和新增 SKU 流程进入 `python_detector_operations.md`。 |
| `calibration_and_roi.md` | 合并 | 标定校验、Dome ROI 定位、ECC 配准和缓存边界进入 `python_detector_operations.md`。 |
| `model_backend.md` | 合并 | ONNX、WideResNet50、PCA、PatchCore/FAISS 和真实模型接入要求进入 `python_detector_operations.md`。 |
| `trace_and_replay.md` | 合并 | trace 文件、训练样本导出、回放和 benchmark 命令进入 `python_detector_operations.md`。 |
| `project_function_call_map.md` | 精简保留 | 原文超过 1000 行，改为模块调用摘要和关键边界，细节以代码和 README 为准。 |
| `v4_architecture_alignment.md` | 保留 | 当前目标架构与生产差距说明，仍是验收入口。 |
| `shm_protocol.md` | 保留 | 协议变更必须同步维护，不能被泛化文档替代。 |

## 推荐阅读顺序

1. 先读根目录 [README](../README.md)，确认当前能力、运行命令和开发约束。
2. 再读 [V4.0 双采集模式架构对齐说明](v4_architecture_alignment.md)，确认目标架构和生产差距。
3. 涉及在线 IPC 时读 [共享内存协议](shm_protocol.md)。
4. 接硬件、上线、测试机联调时读 [C++ 主控部署与硬件运维](cpp_controller_operations.md)。
5. 改 Python 算法、配方、模型、trace 或训练闭环时读 [Python 检测算法与模型运维](python_detector_operations.md)。
6. 需要快速定位代码调用链时读 [项目调用关系摘要](project_function_call_map.md)。
7. 固定双机位 + 三光源工控机交接时读 [工控机上线补齐报告](deployment_readiness_report.md)，确认 COM 口、触发端 IP、光源数量和模型资产状态。
8. 上 Windows 工控机前运行 `uv run python -m tools.validate_deployment_preflight`；放行前再运行 `uv run python -m tools.validate_deployment_preflight --strict-production`。
