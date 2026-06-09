# 部署说明

当前仓库实现了架构文档中的阶段 A，并搭建了阶段 B/C 的最小骨架；Python AI Runtime 已补齐 ONNX Runtime 与可选 FAISS 的工程接入点，真实权重和现场性能参数仍需部署时替换和验证。

已实现：

- C++ 固定布局协议结构体。
- POSIX 共享内存图像/结果 ring buffer。
- C++ 模拟采集和保守结果等待。
- Python 共享内存客户端。
- Python 检测流水线，包括质量门禁、预处理断言、ROI YOLO 接口、ReflectanceCube 构建、ECC 配准、特征构建、ONNX/WideResNet50/PatchCore 工程接入点、融合和规则判定。
- 根目录 `model/` 模型产物占位、生产模型配方模板和模型资产校验工具。

尚未实现：

- 真实 PLC、相机和频闪控制器接入。
- 真实标定参数、ROI 模板和高精度配准参数。
- 真实 YOLO/WideResNet50/FilterClassifier ONNX 权重、PCA 参数、PatchCore memory bank 和 FAISS 索引。
- 长时间生产稳定性压测。

部署前校验真实模型产物：

```bash
uv run python -m tools.validate_model_assets --recipe production_model_example
```

该命令在仓库默认占位模型未替换时应失败，并列出需要替换的 ONNX、PCA、memory bank 和 FAISS 文件。

运行一次模拟 IPC 测试：

```bash
bash tools/run_simulated_ipc.sh
```
