# Seat Surface AOI

汽车座椅表面缺陷检测系统参考实现。

当前实现根据 [seat-defect-inspection-architecture.md](seat-defect-inspection-architecture.md) 搭建，第一阶段重点是共享内存协议、C++/Python 独立进程边界和模拟端到端链路。

## 当前能力

- C++ 固定布局 IPC 协议结构体。
- POSIX 共享内存图像/结果 ring buffer。
- C++ 模拟主控：模拟相机、光源、触发、图像发布和结果等待。
- Python 检测进程：共享内存读取、质量门禁、预处理、ReflectanceCube、特征构建、fake 推理、融合和规则判定。
- 正常模拟图像包返回 `OK`。
- Python detector 不存在或超时时，C++ 保守返回 `RECHECK`，不会误判 `OK`。
- YAML 配方加载与 schema 校验，当前默认配方位于 `python_detector/config/default_recipe.yaml`。
- 配方已覆盖机位、光源顺序、质量阈值、注册策略、模型后端和追溯配置。
- C++ 主控已具备相机、光源、PLC 的可替换接口和模拟驱动，支持光源故障、缺帧、PLC 输出失败等故障注入。
- C++ 运行配置示例位于 `cpp_controller/config/station_runtime.example.conf`。
- Python 检测侧已支持标定文件和 ROI 模板加载，默认 identity 标定位于 `python_detector/config/calibration/`，默认 ROI 位于 `python_detector/config/roi/default_roi.yaml`。
- 模型推理支持 fake 默认后端和 ONNX 可选后端；ONNX 依赖或模型缺失时保守失败，不会静默输出 `OK`。
- 支持本地追溯落盘，`RECHECK`、`ERROR`、`NG` 默认保存 result、quality、registration、feature summary 和 timings。
- 提供模拟回放与 benchmark 工具：`tools/replay_dataset.py`、`tools/benchmark_pipeline.py`。

## 项目规则

项目级 Codex/Agent 规则见 [AGENTS.md](AGENTS.md)。

关键要求：

- 每一次新增、修改、修复代码都必须形成 Git commit 提交记录。
- 每一次代码变更都必须同步更新本 `README.md`。
- 文档、注释和面向用户的说明优先使用中文。

## 运行验证

Python 单元测试：

```bash
python3 -m pytest python_detector/tests
```

协议布局校验：

```bash
python3 -m tools.validate_protocol
```

模拟端到端 IPC：

```bash
bash tools/run_simulated_ipc.sh
```

脚本会构建 C++ 主控，启动 C++ 模拟任务发布，再运行 Python detector 处理一次并回写结果。

C++ 故障注入示例：

```bash
cpp_controller/build/seat_aoi_controller --simulate-missing-frame --wait-ms 200
```

Python 回放和 benchmark：

```bash
python3 -m tools.replay_dataset --count 3 --write-trace
python3 -m tools.benchmark_pipeline --count 10
```

## 对接文档

- [硬件对接说明](docs/hardware_integration.md)
- [配方设计说明](docs/recipe_design.md)
- [标定与 ROI 说明](docs/calibration_and_roi.md)
- [模型后端说明](docs/model_backend.md)
- [追溯与回放说明](docs/trace_and_replay.md)
- [测试机集成清单](docs/test_machine_integration.md)

## 目录结构

```text
seat-surface-aoi/
├── cpp_controller/      # C++ 主控、共享内存 IPC、模拟采集骨架
├── python_detector/     # Python 检测进程和算法流水线骨架
├── docs/                # 共享内存协议和部署说明
└── tools/               # 协议校验和模拟 IPC 脚本
```
