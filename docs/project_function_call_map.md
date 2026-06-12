# 项目调用关系摘要

本文保留原“项目功能调用关系与封装逻辑深度分析”的核心结论，去掉逐函数长篇展开。需要实现细节时以代码、模块 README 和测试为准。

## 总体边界

```text
PLC / 相机 / 频闪 / 机器人
  -> cpp_controller
  -> 共享内存 frame ring
  -> python_detector
  -> 共享内存 result ring
  -> cpp_controller
  -> PLC OK / NG / RECHECK / ERROR
```

- C++ 是实时主控：负责设备、触发、采集、共享内存写入、结果读取和保守降级。
- Python 是独立检测进程：负责质量门禁、预处理、ROI、配准、特征、模型、融合、规则和 trace。
- 在线图像和结果只使用共享内存，不使用 TCP。
- 任意不确定状态不能输出 `OK`。

## 目录职责

| 目录 | 职责 |
| --- | --- |
| `cpp_controller/` | C++ 主控、硬件抽象、采集编排、共享内存 IPC、健康报警和模拟驱动。 |
| `python_detector/` | Python 检测算法、共享内存客户端、配方/标定、模型适配、trace 和单元测试。 |
| `training_tools/` | 离线训练支撑：trace 转样本、embedding、PCA/PatchCore/FAISS、YOLO 导出、评估、回放、benchmark。 |
| `tools/` | 项目级工程校验和联调：协议校验、模型资产校验、架构就绪度检查、模拟 IPC 和 C++ soak 脚本。 |
| `model/` | 真实模型、PCA、PatchCore memory bank 和 FAISS 索引占位目录。 |
| `docs/` | 架构、协议、C++ 运维、Python 算法运维和本摘要。 |

## C++ 在线调用链

```text
main.cpp
  -> StationRuntimeConfig
  -> HardwareFactory
  -> StationController
      -> PlcClient.wait_trigger()
      -> FrameAssembler.capture_job()
          -> RobotClient.wait_pose_ready()
          -> LightController.prepare_sequence()
          -> LightController.arm_hardware_trigger()
          -> CameraWorker.arm()
          -> CameraWorker.wait_frame()
      -> FrameRingBuffer.publish()
      -> ResultRingBuffer.wait_result()
      -> validate detector result
      -> PlcClient.send_decision()
      -> ControllerEventLogger
```

关键封装：

- `StationController`：工位级状态机和结果保守降级。
- `FrameAssembler`：固定机位/机器人飞拍 capture plan、逐视角逐光源采集和结构化采集错误。
- `CameraWorker` / `CameraDevice`：相机初始化、arm、取帧、健康状态和模拟图像生成。
- `LightController`：光源通道参数、硬触发 arm、触发确认和异常关闭。
- `PlcClient`：触发输入、结果输出、ack 和 PLC 健康状态。
- `RobotClient`：机器人 ready/fault、SHOT_ID、PHOTO_TRIGGER 和 TCP 位姿。
- `FrameRingBuffer` / `ResultRingBuffer`：固定布局共享内存读写、状态机、CRC 和超时。

C++ 不能实现深度学习推理；真实硬件驱动接入点见 [C++ 主控部署与硬件运维](cpp_controller_operations.md)。

## 共享内存调用链

Frame 方向：

```text
C++ FrameRingBuffer
  -> EMPTY -> WRITING -> READY
  -> Python ShmClient
  -> READING -> EMPTY
```

Result 方向：

```text
Python ShmClient.publish_result()
  -> result slot READY
  -> C++ ResultRingBuffer.wait_result()
  -> C++ 校验 sequence_id / trigger_id / seat_id / CRC / decision
```

协议变更必须同步：

- `cpp_controller/include/ipc/shm_protocol.hpp`
- `python_detector/ipc/shm_protocol.py`
- `tools.validate_protocol`
- 相关 C++/Python 测试
- [共享内存协议](shm_protocol.md)、根目录 README 和模块 README

## Python 在线调用链

```text
python_detector.detector_main
  -> ShmClient.acquire_job()
  -> SeatSurfaceAoiAlgorithm.inspect()
  -> InspectionPipeline.run()
      -> RecipeManager
      -> ImageQualityGate
      -> Preprocessor
      -> RoiLocator
      -> ReflectanceCubeBuilder / EccRegistration
      -> FeatureBuilder
      -> InferenceEngine / ModelRegistry
      -> FusionEngine
      -> DefectFilter
      -> RuleEngine
      -> TraceWriter
  -> ShmClient.publish_result()
```

关键封装：

- `RecipeManager`：加载 schema、V4 光源映射、模型引用、ROI 和阈值规则。
- `CalibrationManager`：加载标定、ROI 模板和光源对齐矩阵。
- `ImageQualityGate`：缺帧、时间戳、曝光/增益、过曝欠曝、清晰度、运动模糊和光源稳定性。
- `Preprocessor`：MONO8、stride、图像长度、标定版本和 ROI 裁剪/透视展开。
- `RoiLocator`：template、fake YOLO 和 ONNX YOLO 后端。
- `ReflectanceCubeBuilder` / `EccRegistration`：多光源 ROI 组织、固定标定误差检查和 ECC 平移配准。
- `FeatureBuilder`：多光源手工特征、feature summary 和 evidence lights。
- `InferenceEngine`：fake、ONNX detection rows、WideResNet50 embedding、PCA、PatchCore/FAISS。
- `FusionEngine` / `DefectFilter` / `RuleEngine`：候选融合、二阶段过滤、OK/NG/RECHECK/ERROR 判定。
- `TraceWriter`：保存 job、result、quality、ROI、registration、feature、fusion、timings、error、ROI 图和 overlay。

Python 不能控制 PLC、相机或频闪；算法和模型运维细节见 [Python 检测算法与模型运维](python_detector_operations.md)。

## 离线闭环

```text
共享内存多光源图像或 trace/
  -> training_tools.collect_shm_dataset / training_tools.collect_trace_dataset
  -> dataset_manifest.jsonl
  -> training_tools.export_wideresnet_embedding
  -> training_tools.extract_embeddings
  -> training_tools.train_patchcore_assets
  -> model/*
  -> training_tools.evaluate_pipeline
```

模型资产生成入口：

- `training_tools.train_roi_yolo`
- `training_tools.train_supervised_yolo`
- `training_tools.export_wideresnet_embedding`
- `training_tools.train_patchcore_assets`

离线工具只消费 Python 检测层公开入口和 trace 产物，不反向耦合在线 detector，也不控制 PLC、相机或频闪。

## 安全闭环清单

- 缺帧、超时、协议错误、CRC 错误、slot 满、质量门禁失败、模型异常都不能输出 `OK`。
- C++ 必须校验 Python 结果中的 `sequence_id`、`trigger_id`、`seat_id`、decision、质量状态、错误码和缺陷数量。
- Python 必须校验 shape、dtype、channel order、stride、bbox 格式、光源、机位、时间戳和配方引用。
- PatchCore 只能作为 safety net，低置信但可疑样本走 `RECHECK`。
- 共享内存协议变更必须双端同步并运行 `tools.validate_protocol`。

## 阅读顺序

1. [README](../README.md)
2. [docs 总览](README.md)
3. [V4.0 双采集模式架构对齐说明](v4_architecture_alignment.md)
4. [共享内存协议](shm_protocol.md)
5. [C++ 主控部署与硬件运维](cpp_controller_operations.md)
6. [Python 检测算法与模型运维](python_detector_operations.md)
7. [C++ 主控 README](../cpp_controller/README.md)
8. [Python 检测算法层导览](../python_detector/README.md)
