# AGENTS.md

## 项目定位

本项目是汽车座椅表面缺陷检测系统的参考实现，在线主链路遵循 `seat-defect-inspection-architecture.md`：

- C++ 负责 PLC、相机、频闪、共享内存写入、结果读取和节拍控制。
- Python 作为独立检测进程，负责图像质量门禁、预处理、多光源特征、模型推理、融合和规则判定。
- C++ 与 Python 在线交换图像和结果必须使用共享内存，不使用 TCP。

## 必须遵守的工作规则

1. 每一次新增代码、修改代码、修复代码都必须形成 Git commit 提交记录。
2. 任何代码变更都必须同步更新根目录 `README.md`，说明新增能力、行为变化、运行方式或验证方式。
3. 文档、注释、提交说明和面向用户的说明优先使用中文。
4. 不允许用 Python 控制 PLC、相机或频闪；Python 只负责检测链路。
5. 不允许在 C++ 主控中实现深度学习推理；模型和算法逻辑属于 Python 检测进程。
6. 任何不确定状态、超时、缺帧、协议错误、CRC 错误、质量门禁失败都不能输出 `OK`，必须输出 `RECHECK` 或 `ERROR`。
7. 修改共享内存协议时，必须同步更新 C++ 与 Python 两侧结构、协议校验工具和相关测试。

## 推荐执行顺序

1. 先阅读 `seat-defect-inspection-architecture.md` 和本文件。
2. 检查当前 git 状态，确认是否存在用户未提交改动。
3. 按最小可验证范围修改代码。
4. 同步更新 `README.md`。
5. 运行相关验证命令。
6. 提交 commit，并在提交信息中说明变更范围和验证结果。

## 常用验证命令

```bash
python3 -m pytest python_detector/tests
python3 -m tools.validate_protocol
bash tools/run_simulated_ipc.sh
```

如果本机没有 `cmake`，`run_simulated_ipc.sh` 会自动回退到 `clang++` 编译 C++ 主控。

