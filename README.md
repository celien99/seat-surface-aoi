# Seat Surface AOI

> 汽车座椅表面缺陷检测系统参考实现。项目采用 **C++ 实时主控 + Python 独立检测进程 + POSIX 共享内存 IPC**，同时覆盖固定机位多光源和机器人飞拍多光源两种在线采集模式。

![Seat Surface AOI hero](docs/assets/readme-hero.png)

<p align="center">
  <img alt="C++17" src="https://img.shields.io/badge/C%2B%2B-17-00599C?style=for-the-badge&logo=cplusplus&logoColor=white">
  <img alt="Python 3.10" src="https://img.shields.io/badge/Python-3.10-3776AB?style=for-the-badge&logo=python&logoColor=white">
  <img alt="ONNX Runtime" src="https://img.shields.io/badge/ONNX-Runtime-005CED?style=for-the-badge&logo=onnx&logoColor=white">
  <img alt="FAISS" src="https://img.shields.io/badge/FAISS-Optional-00A86B?style=for-the-badge">
  <img alt="IPC" src="https://img.shields.io/badge/IPC-Shared%20Memory-6F42C1?style=for-the-badge">
</p>

## 项目定位

Seat Surface AOI 面向汽车座椅生产线表面缺陷检测，重点验证一条可生产化落地的在线链路：

- **C++ 主控**：PLC、相机、频闪、机器人 pose/shot、触发同步、共享内存写入、结果读取和保守降级。
- **Python 检测**：质量门禁、ROI、ECC 配准、多光源特征、ONNX 推理、PatchCore/FAISS 安全网、融合判定和 trace。
- **共享内存 IPC**：在线图像和检测结果不走 TCP；协议错误、CRC 错误、超时、缺帧和质量失败都不能输出 `OK`。

![Seat Surface AOI system overview](docs/assets/readme-system-overview.png)

## 核心能力

| 能力 | 当前状态 |
| --- | --- |
| 双采集模式 | 固定机位 `fixed_camera` 与机器人飞拍 `robot_flyshot` 共用一套 C++ Capture Plan 和 Python 检测链路。 |
| 视角级串行 TDM | 每个检测视角按 `light_order` 完成多光源采集后再切换下一视角，避免光源互相污染。 |
| 共享内存协议 | C++/Python 双端固定布局结构体、frame/result ring buffer、CRC 和协议校验工具。 |
| V4 算法接口 | Dome ROI YOLO、ECC、WideResNet50 embedding、PCA、PatchCore KNN、FAISS 可选加速。 |
| 数据闭环 | trace、ROI 图、overlay、manifest、embedding、PatchCore/FAISS 资产训练、回放与 benchmark。 |

## 快速验证

```bash
uv sync --group dev
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_architecture_readiness --scope reference
bash tools/run_simulated_ipc.sh
```

模拟 IPC 会构建 C++ 主控，发布一次多光源图像包，Python detector 读取共享内存并写回结果。正常模拟链路应返回 `OK`；故障注入、协议错误或 detector 超时必须返回 `RECHECK` 或 `ERROR`。

## 常用入口

```bash
# 固定机位模拟链路
bash tools/run_simulated_ipc.sh

# 机器人飞拍模拟链路
bash tools/run_simulated_ipc.sh --config cpp_controller/config/station_runtime.robot_flyshot.example.conf

# 真实模型资产上线前检查
uv run python -m tools.validate_model_assets --recipe production_model_example

# Python detector 在线入口
uv run python -m python_detector.detector_main --once --timeout-ms 8000
uv run seat-aoi-detector --once --timeout-ms 8000
```

## 目录一览

```text
seat-surface-aoi/
├── cpp_controller/      # C++ 主控、采集调度、硬件抽象、共享内存 IPC
├── python_detector/     # Python 检测算法、配方、标定、模型适配、trace
├── training_tools/      # trace 转样本、embedding、PCA/PatchCore/FAISS、评估
├── model/               # 真实模型产物占位：YOLO、WideResNet50、PCA、PatchCore
├── docs/                # 架构、协议、C++ 运维、Python 算法运维
└── tools/               # 协议校验、模型资产校验、模拟 IPC、架构就绪度检查
```

## 文档入口

- [V4.0 双采集模式架构对齐说明](docs/v4_architecture_alignment.md)
- [共享内存协议](docs/shm_protocol.md)
- [C++ 主控部署与硬件运维](docs/cpp_controller_operations.md)
- [Python 检测算法与模型运维](docs/python_detector_operations.md)
- [Python 检测算法层导览](python_detector/README.md)
- [模型产物目录说明](model/README.md)

## 安全约束

- Python 不控制 PLC、相机、机器人或频闪。
- C++ 主控不实现深度学习推理。
- 任意不确定状态不得输出 `OK`。
- 共享内存协议变更必须同步更新 C++、Python、校验工具和测试。

## 许可

当前仓库尚未声明开源许可证。正式公开发布前建议补充 `LICENSE` 文件。
