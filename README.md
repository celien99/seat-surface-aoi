# Seat Surface AOI

汽车座椅表面缺陷检测系统参考实现。项目以生产线在线 AOI 场景为目标，采用 **C++ 实时主控 + Python 独立检测进程 + 共享内存 IPC** 的架构，覆盖多机位、多光源频闪采集、质量门禁、ROI 处理、多光源特征、模型推理、融合决策和追溯验证链路。

> 当前项目以 V4.0 集成 ONNX + FAISS 方案架构图作为目标架构与后续验收口径。已有实现覆盖控制通信骨架、基础检测流水线、V4 光源语义映射、Dome ROI 定位接口、ECC 配准、ONNX/WideResNet50/PCA/PatchCore 工程接入点、模型资产校验、全链路 trace 和离线训练样本支撑工具；真实硬件 SDK、真实模型权重、MES/报警和平台化监控仍需按现场项目接入。

![汽车座椅表面缺陷检测系统整体架构图 V4.0 集成 ONNX + FAISS 方案](docs/assets/architecture-v4.png)

## 核心原则

- **C++ 负责实时控制**：PLC、相机、频闪、触发调度、共享内存写入、结果读取和节拍控制。
- **C++ 采集策略采用串行 TDM**：当前主控按“逐机位串行、逐光源串行”执行频闪采集；每个机位独立完成全部光源序列后才切换到下一个机位，单次频闪只采当前机位图像。
- **Python 负责检测算法**：质量门禁、预处理、ROI、配准、特征、模型推理、融合和规则判定。
- **在线图像与结果只走共享内存**：C++ 与 Python 主链路不使用 TCP。
- **不确定结果保守处理**：超时、缺帧、协议错误、CRC 错误、质量失败和模型异常不能输出 `OK`，必须输出 `RECHECK` 或 `ERROR`。
- **协议变更双端同步**：共享内存协议修改必须同步更新 C++、Python、协议校验工具和测试。

## 当前能力

| 模块 | 当前状态 |
|---|---|
| 光学采集抽象 | 支持多机位、多光源模拟采集；当前采用串行 TDM 频闪策略，按机位独立完成 `light_order` 后再切换机位；默认光源为 `DIFFUSE`、`POLAR_DIFFUSE`、`HIGH_LEFT`、`HIGH_RIGHT` |
| C++ 主控 | 支持 PLC 抽象、相机/光源模拟驱动、`camera_exposure_output` 硬触发同步模式、逐机位/逐光源串行采集、故障注入和保守降级 |
| 共享内存 IPC | POSIX shared memory，固定布局结构体，frame/result ring buffer，CRC 与协议布局校验 |
| Python 检测进程 | 支持共享内存读取、质量门禁、Dome ROI 定位接口、ROI 裁剪/透视展开、固定标定或 ECC 配准、特征构建、推理、融合、缺陷过滤和规则判定 |
| 模型后端 | 支持 fake、ONNX detection rows、统计 embedding、ONNX WideResNet50 embedding、PCA 投影和 PatchCore safety net；PatchCore 优先尝试 FAISS，缺索引或缺依赖时回退 exact KNN 并写入 trace |
| 模型产物 | 根目录 `model/` 提供 YOLO、监督检测、WideResNet50、PCA、PatchCore memory bank 和可选 FAISS 索引占位；`production_model.example.yaml` 展示真实模型配方 |
| 追溯与工具 | 支持 trace、ROI 定位报告、ECC 报告、embedding/PCA/anomaly summary、ROI 图落盘、overlay、Trace 转训练样本、回放、benchmark、PatchCore memory bank 构建、模型资产校验和模拟 IPC 验证 |

## V4.0 对齐状态

当前代码已经对齐 V4.0 的进程边界、共享内存通信、安全降级要求和主要算法接口；生产落地仍需要接入真实权重、真实硬件和现场平台服务。

| V4.0 架构模块 | 当前实现 |
|---|---|
| 1. 光学采集层 | 部分对齐：已有多光源/多机位模拟链路和 V4 语义光源映射；C++ 当前按串行 TDM 策略执行“机位A全光源→机位B全光源”的独立频闪采集，真实硬件 SDK 集成仍需项目化接入 |
| 2. 控制与通信层 | 基本对齐：C++ 控制，Python 不控制 PLC/相机/频闪，在线链路使用共享内存 |
| 3.1 ROI 定位 | 工程接入点对齐：支持 Dome 语义光源、模板/fake YOLO/ONNX YOLO 后端、真实 YOLO 占位目录和 YOLO row 到 ROI 模板坐标转换；真实权重和训练评估需现场产出 |
| 3.2 ROI 裁剪与配准 | 基本对齐：已有 ROI 裁剪/透视展开、固定标定误差检查和 ECC 在线配准报告 |
| 3.3 特征提取 | 工程接入点对齐：已有多光源手工特征、统计 embedding、ONNX WideResNet50 embedding 入口和占位产物路径；真实权重和层选择需按模型接入 |
| 3.4 特征融合与降维 | 基本对齐：支持 unified embedding summary、PCA 参数加载、版本校验和投影 |
| 3.5 PatchCore 异常检测 | 工程接入点对齐：支持 memory bank JSON、coreset 工具、PCA、可选 FAISS 索引、exact KNN 回退、anomaly score 和规则阈值 |
| 4. 后处理与决策层 | 部分对齐：已有融合、缺陷过滤模块和规则判定，MES/报警接口仍需扩展 |
| 5. 系统管理维护 | 部分对齐：已有配置、模型、trace 和工具文档，完整数据/模型/监控平台不在当前实现范围内 |
| 6. AI Runtime 与依赖 | 工程接入点对齐：ONNX Runtime 适配、YOLO/WideResNet50/FilterClassifier ONNX 路径、FAISS 可选索引、OpenCV/NumPy 等基础依赖按部署环境安装 |

详见 [V4.0 架构对齐说明](docs/v4_architecture_alignment.md)。

## 快速开始

### 环境要求

- macOS 或 Linux
- Python 3.10+
- uv 0.11+，用于 Python 层依赖、虚拟环境和 lockfile 管理
- C++17 编译器
- CMake 3.16+，若本机没有 CMake，模拟 IPC 脚本会回退到 `clang++`

### Python 算法环境

Python 检测层已按独立算法模块规范化，根目录 `pyproject.toml` 和 `uv.lock` 统一管理包元数据、依赖分组、锁定版本、测试配置和命令行入口。`.python-version` 固定默认 Python 版本为 3.10。

```bash
# 开发环境，包含 pytest、numpy 和 ruff
uv sync --group dev

# 测试环境，包含 pytest 和 numpy
uv sync --group test

# 启用 ONNX/YOLO/WideResNet50 后端时再安装
uv sync --group dev --extra onnx

# 启用 PatchCore FAISS 索引加速时再安装
uv sync --group dev --extra onnx --extra faiss

# 生产/测试机按锁文件安装基础链路
uv sync --locked --no-dev
```

默认 fake/statistical/PatchCore exact KNN 参考链路只依赖 PyYAML，不依赖 ONNX Runtime 或 FAISS；测试组额外包含 numpy 用于 ONNX 输出解析单元测试。缺少可选后端依赖、模型文件或输出解码配置时，检测结果必须保守返回 `RECHECK` 或 `ERROR`。

`pyproject.toml` 仍保持标准 Python 项目格式，必要时可用 `python3 -m pip install -e .` 做兼容安装；日常开发、CI 和测试机复现优先使用 uv。

Python 算法模块公开入口：

- `python_detector.SeatSurfaceAoiAlgorithm`：不包含 IPC 的纯算法入口，适合回放、测试和离线验证。
- `python_detector.InspectionPipeline`：质量门禁、预处理、特征、推理、融合和规则判定的流水线编排入口。
- `python_detector.detector_main` / `seat-aoi-detector`：在线检测进程入口，只负责共享内存循环和结果发布。

### 离线训练支撑工具

`training_tools/` 是独立离线工具包，只调用 Python 检测层公开入口和 trace 产物，不反向依赖在线 detector，也不控制 PLC、相机或频闪。当前用于生成训练样本 manifest、回放评估、性能 benchmark 和 PatchCore memory bank 资产准备；外部 Filter 模型训练项目可直接消费这些样本和模型资产。

`trace/` 和 `datasets/` 是运行期追溯与训练样本输出目录，默认不提交 Git；真实现场数据、训练样本和大模型权重必须走外部数据/模型管理流程。

### 运行验证

```bash
uv run pytest
uv run python -m tools.validate_protocol
bash tools/run_simulated_ipc.sh
```

模拟 IPC 脚本会构建 C++ 主控，启动一次模拟采集任务，再运行 Python detector 读取共享内存并回写结果。正常模拟图像包应返回 `OK`；故障注入、协议错误或 detector 超时应返回 `RECHECK` 或 `ERROR`。

### 常用命令

```bash
# Python 测试
uv run pytest

# 校验 C++ / Python 共享内存协议布局
uv run python -m tools.validate_protocol

# 校验真实模型产物是否已替换占位文件
uv run python -m tools.validate_model_assets --recipe production_model_example

# 模拟端到端 IPC
bash tools/run_simulated_ipc.sh

# 在线 Python detector 入口
uv run python -m python_detector.detector_main --once --timeout-ms 8000
uv run seat-aoi-detector --once --timeout-ms 8000

# Trace 转训练样本 manifest 和 ROI 图像副本
uv run python -m training_tools.collect_trace_dataset --trace-root trace --output datasets/seat_trace_v1

# Python 回放
uv run python -m training_tools.replay_dataset --count 3 --write-trace

# Python benchmark
uv run python -m training_tools.benchmark_pipeline --count 10

# PatchCore memory bank 构建示例
uv run python -m training_tools.build_patchcore_memory_bank --input embeddings.jsonl --output model/patchcore/seat_patchcore_bank.json --version bank_v1 --coreset-ratio 0.1 --pca-version pca_seat_v1 --faiss-enabled

# C++ 故障注入示例
cpp_controller/build/seat_aoi_controller --simulate-missing-frame --wait-ms 200
cpp_controller/build/seat_aoi_controller --simulate-light-fault --wait-ms 200
cpp_controller/build/seat_aoi_controller --simulate-trigger-timeout --trigger-timeout-ms 50
```

## 目录结构

```text
seat-surface-aoi/
├── cpp_controller/      # C++ 主控、采集调度、共享内存 IPC、模拟硬件驱动
├── model/               # 真实模型产物占位目录：YOLO、监督检测、WideResNet50、PCA、PatchCore、可选 FAISS
├── python_detector/     # 独立 Python 检测算法模块、V4 ROI/ECC/embedding/PCA/PatchCore 流水线、配方、测试
├── training_tools/      # 离线训练支撑：Trace 转样本、回放、benchmark、PatchCore memory bank 构建
├── docs/                # 架构、协议、部署、硬件和模型文档
├── tools/               # 协议校验、模型资产校验、模拟 IPC 和旧离线命令兼容包装
├── pyproject.toml       # Python 算法模块包元数据、依赖分组、测试和 lint 配置
├── uv.lock              # Python 依赖锁文件
├── .python-version      # uv/pyenv 默认 Python 版本
└── AGENTS.md            # 项目级协作与工程约束
```

## 关键文档

- [V4.0 架构对齐说明](docs/v4_architecture_alignment.md)
- [模型产物目录说明](model/README.md)
- [共享内存协议](docs/shm_protocol.md)
- [硬件对接说明](docs/hardware_integration.md)
- [C++ 主控硬件集成与使用手册](docs/cpp_controller_hardware_manual.md)
- [配方设计说明](docs/recipe_design.md)
- [标定与 ROI 说明](docs/calibration_and_roi.md)
- [模型后端说明](docs/model_backend.md)
- [Python 检测算法模块规范](docs/python_detector_module.md)
- [Python 检测算法层导览](python_detector/README.md)
- [追溯与回放说明](docs/trace_and_replay.md)
- [测试机集成清单](docs/test_machine_integration.md)
- [部署说明](docs/deployment.md)

## 开发约束

本仓库遵循 [AGENTS.md](AGENTS.md) 中的项目规则。重要约束如下：

- 每次代码变更必须同步更新 `README.md`。
- 每次新增、修改、修复代码必须形成 Git commit。
- Python 不允许控制 PLC、相机或频闪。
- C++ 主控不允许实现深度学习推理。
- 任意不确定状态不得输出 `OK`。
- 修改共享内存协议时必须同步更新 C++、Python、协议校验工具和相关测试。
- 修改 `python_detector` 时必须同步更新 [Python 检测算法层导览](python_detector/README.md)。

## 许可

当前仓库尚未声明开源许可证。正式公开发布前建议补充 `LICENSE` 文件。
