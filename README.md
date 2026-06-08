# Seat Surface AOI

汽车座椅表面缺陷检测系统参考实现。

当前实现根据 [seat-defect-inspection-architecture.md](docs/seat-defect-inspection-architecture.md) 搭建，第一阶段重点是共享内存协议、C++/Python 独立进程边界和模拟端到端链路。

## 当前能力

- C++ 固定布局 IPC 协议结构体。
- POSIX 共享内存图像/结果 ring buffer。
- C++ 模拟主控：通过 PLC 抽象接收模拟外部触发，默认采用 `camera_exposure_output` 硬触发同步模式，按光源顺序 arm 频闪和相机，并行采集多机位模拟图像，完成图像发布和结果等待。
- C++ 主控在 PLC 输出前会校验检测结果语义：`sequence_id`、`trigger_id`、`seat_id`、decision、质量状态、错误码和缺陷数量不一致时统一降级为 `RECHECK`，不会输出 `OK`。
- Frame ring 发布会扫描可用 `EMPTY` slot，单个 `READING`/坏 slot 不会阻塞其它空闲 slot。
- Result ring 对协议、payload 和 CRC 错误会立即返回真实错误码，不再等待到 detector timeout。
- Python 检测进程：共享内存读取、质量门禁、预处理、ReflectanceCube、特征构建、fake 推理、融合和规则判定。
- Python 质量门禁会校验配方启用机位完整性、SKU 一致性和必需光源；缺机位、重复机位、未知机位或缺关键光源都会返回 `RECHECK`，不会输出 `OK`。
- Python 质量门禁会校验必需光源的采集一致性，包括时间戳跨度、时间戳单调性、帧序号重复、曝光差和增益差；会在预处理前拒绝非 `MONO8/UINT8/MONO/1ch`、stride 小于有效行宽或图像长度不足的帧；灰度、饱和和清晰度统计只使用有效像素宽度，不把 stride padding 当成图像内容；异常采集包会返回 `RECHECK`。
- Python 检测进程读取坏 frame slot 时会释放输入 slot；检测、配方或模型异常会回写 `ERROR`/`RECHECK`，不会让共享内存 slot 长期停留在 `READING`。
- V2 生产标准默认使用 `DIFFUSE`、`POLAR_DIFFUSE`、`HIGH_LEFT`、`HIGH_RIGHT` 四个必需光源，生成 `ch0_diffuse` 到 `ch4_high_max_min` 的 5 通道标准特征。
- 规则判定使用配方中的类别阈值 `ng_score`、`recheck_score` 和 `min_area_px`；机位级 `light_order` 会进入 ReflectanceCube 和特征构建。
- Python 预处理会按 ROI 模板裁剪 MONO8 图像并保留 `bbox_xyxy_pixel` 原图坐标；ROI 越界、ROI 输出尺寸不一致、标定尺寸不一致会保守失败。
- ROI 预处理支持轴对齐矩形快速裁剪和四点多边形透视展开，可将倾斜 ROI 规整到 `output_size` 后进入特征和模型链路；预处理会保留 ROI 到原图、原图到 ROI 的双向矩阵，用于模型 bbox 和追溯 overlay 坐标映射。
- ReflectanceCube 会使用标定文件中的 `light_alignment.matrix_3x3` 计算 ROI 角点配准误差，超过 `quality.max_registration_error_px` 时返回 `RECHECK`。
- FeatureBuilder 会为每个 ROI 模型构建 NCHW float 输入张量，通道顺序、输入缩放和模型输出解码方式由配方 `models.*` 字段声明。
- ONNX 后端支持可配置 `detection_rows` 输出解码，模型输出 bbox 先按 ROI 局部坐标解释，再通过 ROI 坐标矩阵映射为原图 `bbox_xyxy_pixel`；输入/输出缺失、类别越界、bbox 越界/反向/非有限值或未配置输出解码时会保守失败，不会静默 clamp 后输出 `OK`。
- FusionEngine 会按 `fusion.iou_threshold`、`class_aware` 和 `max_candidates_per_roi` 对同机位同 ROI 候选做 IoU NMS，合并重叠候选的证据光源并在 trace 中记录输入、输出和压制数量。
- Python 回写缺陷结果时会把 `camera_id` 和由特征通道反查得到的真实 `evidence_lights` 映射为共享内存协议中的机位/光源索引，便于 C++ 侧追溯缺陷来源。
- 低角度暗场、前后高角度和 NIR 作为可选增强光源，不作为主链路输出 `OK` 的默认前置依赖。
- 正常模拟图像包返回 `OK`。
- Python detector 不存在或超时时，C++ 保守返回 `RECHECK`，不会误判 `OK`。
- YAML 配方加载与 schema 校验，当前默认配方位于 `python_detector/config/default_recipe.yaml`。
- 配方已覆盖机位、光源顺序、质量阈值、注册策略、ROI 级主模型、unknown safety net 模型、模型后端和追溯配置；schema 会拒绝越界分数阈值、负面积阈值和 `recheck_score > ng_score` 的不安全规则配置。
- C++ 主控已具备相机、光源、PLC 的可替换接口和模拟驱动，支持触发超时、光源故障、缺帧、PLC 输出失败等故障注入。
- 频闪同步支持 `camera_exposure_output` 默认模式：C++ 负责配置光源、arm 相机和频闪、等待图像与判断故障；模拟链路用相机曝光输出触发频闪。保留 `software` 模式用于纯软件调度测试。
- C++ 单个共享内存 frame slot 承载一个座椅任务的所有机位、所有光源图像；Python 检测进程按 `camera_index` 组装 `CameraBundle`。
- C++ 运行配置示例位于 `cpp_controller/config/station_runtime.example.conf`。
- Python 检测侧已支持标定文件和 ROI 模板加载，默认 identity 标定位于 `python_detector/config/calibration/`，默认 ROI 位于 `python_detector/config/roi/default_roi.yaml`；标定缓存按 `camera_id`、`calibration_id` 和 ROI 模板路径隔离，避免多 SKU/多 ROI 模板复用错误 ROI。
- 模型推理支持 fake 默认后端和 ONNX 可选后端；ONNX 依赖、模型缺失、输入配置或输出解码异常时保守失败，不会静默输出 `OK`。
- PatchCore 只能配置为 unknown defect safety net，不能作为全座椅或 ROI 主检测模型。
- 支持本地追溯落盘，`RECHECK`、`ERROR`、`NG` 默认保存 result、quality、registration、feature summary、timings 和 error context。
- 追溯会保存 ROI 单光源灰度图 `.pgm`；存在缺陷时会生成带红色 bbox 的 `.ppm` overlay，bbox 使用原图 `bbox_xyxy_pixel` 映射回 ROI 坐标。
- `trace.save_ok_ratio` 使用基于座椅和序列号的确定性抽样保存 OK 样本；`NG`、`RECHECK`、`ERROR` 按策略默认保存。
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
cpp_controller/build/seat_aoi_controller --simulate-light-fault --wait-ms 200
cpp_controller/build/seat_aoi_controller --simulate-trigger-timeout --trigger-timeout-ms 50
```

C++ 运行配置示例支持 `recipe_id`、逗号分隔的 `light_order`、`trigger_sync_mode`、触发 timeout 和批次数：

```bash
cpp_controller/build/seat_aoi_controller --config cpp_controller/config/station_runtime.example.conf
```

连续模拟 PLC 触发 3 件：

```bash
cpp_controller/build/seat_aoi_controller --loop --max-jobs 3 --wait-ms 8000
```

Python 回放和 benchmark：

```bash
python3 -m tools.replay_dataset --count 3 --write-trace
python3 -m tools.benchmark_pipeline --count 10
```

## 对接文档

- [硬件对接说明](docs/hardware_integration.md)
- [C++ 主控硬件集成与使用手册](docs/cpp_controller_hardware_manual.md)
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
