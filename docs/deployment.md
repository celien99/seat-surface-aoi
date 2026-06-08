# 部署说明

当前仓库实现了架构文档中的阶段 A，并搭建了阶段 B/C 的最小骨架。

已实现：

- C++ 固定布局协议结构体。
- POSIX 共享内存图像/结果 ring buffer。
- C++ 模拟采集和保守结果等待。
- Python 共享内存客户端。
- Python fake 检测流水线，包括质量门禁、预处理断言、ReflectanceCube 构建、特征构建、fake 推理、融合和规则判定。

尚未实现：

- 真实 PLC、相机和频闪控制器接入。
- 真实标定 YAML 解析和 ROI 矫正。
- ONNX Runtime、TensorRT 或 PyTorch 模型后端。
- 长时间生产稳定性压测。

运行一次模拟 IPC 测试：

```bash
bash tools/run_simulated_ipc.sh
```
