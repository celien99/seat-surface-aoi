# Python 检测算法层导览

本文面向新开发者和 Agent，用于快速理解 `python_detector` 包的职责、文件结构、主流程、扩展点和维护规则。凡是新增、修改或重构 `python_detector` 下的算法、配置、模型后端、IPC 解析、trace 或测试结构，都需要同步更新本文。

## 模块职责

`python_detector` 是汽车座椅表面 AOI 的 Python 检测算法层，职责是把 C++ 主控写入共享内存的 `SeatInspectionJob` 转换为 `InspectionResult`：

```text
SeatInspectionJob
  -> ImageQualityGate
  -> Preprocessor / RoiLocator
  -> ReflectanceCubeBuilder / EccRegistration
  -> FeatureBuilder
  -> InferenceEngine / ModelRegistry
  -> FusionEngine
  -> RuleEngine
  -> InspectionResult
```

Python 只负责检测链路，不控制 PLC、工业相机或频闪；在线图像和结果交换必须通过共享内存。任意缺帧、CRC 错误、协议错误、质量门禁失败、ROI/配准失败或模型异常都不能输出 `OK`，必须返回 `RECHECK` 或 `ERROR`。

离线训练样本生成、真实 ROI 图 embedding 提取、PCA/PatchCore/FAISS 资产训练、ROI/监督 YOLO ONNX 导出、manifest 评估、回放和 benchmark 放在根目录 `training_tools/`。调用方向只能是 `training_tools -> python_detector`，在线算法层不能 import 离线训练工具；训练、回放和 benchmark 不再通过 `tools/` 暴露兼容入口。

## 依赖管理

Python 层使用项目根目录的 `pyproject.toml` 和 `uv.lock` 管理依赖：

- 默认运行依赖：`PyYAML`，用于配方、标定和 ROI YAML。
- `test` dependency group：`pytest`、`numpy`，用于单元测试和 ONNX 输出解析测试。
- `dev` dependency group：`pytest`、`numpy`、`ruff`，用于开发验证。
- `onnx` extra：`numpy`、`onnxruntime`，仅在启用 ONNX detection、YOLO ROI 或 WideResNet50 embedding 后端时需要。
- `faiss` extra：`faiss-cpu`、`numpy`，仅在 PatchCore 启用 FAISS 索引加速时需要；未安装或索引缺失时回退 exact KNN。

常用命令：

```bash
uv sync --group dev
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
uv run python -m tools.validate_architecture_readiness --scope reference
uv run python -m tools.validate_deployment_preflight
uv run python -m python_detector.detector_main --once --timeout-ms 8000
uv run python -m python_detector.detector_main \
  --config cpp_controller/config/station_runtime.lab_manual.example.conf \
  --once --timeout-ms 8000
```

端到端模拟可使用 `bash tools/run_simulated_ipc.sh`；Windows 工控机或跨平台联调使用 `uv run python tools/run_simulated_ipc.py`。两个入口都会先启动 C++ 主控，再启动 detector 读取共享内存任务并写回结果。带 `--config` 运行时，脚本会把同一份 C++ 运行配置传给 detector，detector 会读取 `slot_count`、`frame_slot_size` 和 `result_slot_size`，确保 4096 x 3072 固定机位高分辨率图像不会因为 Python 仍使用默认 16 MB slot 而布局不匹配。

## 部署打包

根目录 `tools/package_release.sh` 会把 Python 在线检测层和模型目录一起放入离线部署包。包内 Python 相关内容包括：

- `python_detector/`：在线 detector、IPC 客户端、配方、标定、ROI 模板、算法流水线、模型后端和测试。
- `display_app/`：PySide6/QML 展示前端，读取 detector display 通道；运行前需要安装 `display` extra。
- `training_tools/`：离线回放、benchmark、embedding、PCA/PatchCore/FAISS 资产生成工具。
- `model/`：默认集成根目录 `model/`；生产打包前必须先把真实模型产物替换到该目录。
- `pyproject.toml` 和 `uv.lock`：用于在目标环境恢复 Python detector 依赖。

参考联调包可以生成但不代表生产模型就绪：

```bash
bash tools/package_release.sh
```

生产包必须先替换根目录 `model/` 下的 1 字节占位 ONNX/PCA/FAISS 文件，然后直接打包：

```bash
bash tools/package_release.sh
```

解包后可运行：

```bash
bash validate_package.sh
bash run_packaged_simulated_ipc.sh
```

打包不会包含现场 `trace/`、训练数据集、日志、`.venv` 或本地缓存。Python detector 仍只负责检测链路，不控制 PLC、相机、机器人或频闪。

## 文件结构

```text
python_detector/
├── __init__.py                 # 包级公开 API，导出算法入口、核心数据类型和 RecipeManager
├── algorithm.py                # SeatSurfaceAoiAlgorithm 纯算法 facade，不包含共享内存循环
├── detector_main.py            # 在线 Python detector 进程入口，负责 ShmClient 循环和结果发布
├── paths.py                    # 包内配置、标定、ROI 模板路径解析，兼容仓库相对路径
├── py.typed                    # 标记该包提供类型信息
├── config/
│   ├── default_recipe.yaml     # 默认固定机位检测配方
│   ├── robot_flyshot_recipe.yaml # 机器人飞拍示例配方，同一末端相机对应多个 pose_id
│   ├── production_recipe.yaml  # 固定机位生产完整模型链路配方，RecipeManager 默认加载
│   ├── production_robot_flyshot_recipe.yaml # 机器人飞拍生产完整模型链路配方
│   ├── production_model.example.yaml # 真实模型接入参考模板，不参与默认加载
│   ├── recipe_schema.py        # 配方 dataclass、YAML 加载、字段校验和模型引用校验
│   ├── calibration_manager.py  # 标定文件、ROI 模板加载和几何合法性校验
│   ├── calibration/            # 按 camera_id 存放模拟/生产标定模板，机器人飞拍按 pose 使用不同 calibration_id
│   └── roi/                    # ROI 模板，生产模板需按现场标定替换
├── ipc/
│   ├── data_types.py           # LightFrame、CameraBundle、SeatInspectionJob、InspectionResult 等数据结构
│   ├── shm_protocol.py         # Python 侧共享内存协议常量、结构体布局、CRC、枚举
│   ├── shared_memory_map.py    # POSIX/Windows 共享内存名称映射和 mmap 打开封装
│   └── shm_client.py           # 读取共享内存任务和写回结果
├── pipeline/
│   ├── pipeline.py             # InspectionPipeline 主编排
│   ├── quality_gate.py         # 图像质量门禁：缺帧、曝光、锐度、运动模糊、时间戳等
│   ├── preprocessor.py         # 元数据断言、标定匹配、ROI 裁剪和透视展开
│   ├── roi_locator.py          # Dome 语义光源 ROI 定位，支持 template/fake_yolo/onnx_yolo
│   ├── reflectance_cube.py     # 多光源 ROI 对齐后的 ReflectanceCube 构建
│   ├── ecc_registration.py     # ECC 风格平移搜索和非基准光源 ROI 重采样
│   ├── feature_builder.py      # 多光源特征和 NCHW tensor 构建
│   ├── fusion_engine.py        # 候选框融合、NMS、候选数量限制
│   ├── defect_filter.py        # 类别阈值、面积阈值等缺陷过滤
│   └── rule_engine.py          # OK/NG/RECHECK/ERROR 规则判定
├── models/
│   ├── inference_engine.py     # Fake/ONNX/PatchCore 后端统一推理入口和模型缓存
│   ├── onnx_runtime.py         # ONNX Runtime session、numpy 输入和统一保守错误包装
│   ├── embedding.py            # statistical 与 onnx_wideresnet50 embedding 入口
│   ├── pca.py                  # PCA JSON 参数加载、版本校验和投影
│   └── patchcore.py            # PatchCore memory bank exact KNN 参考实现
├── trace/
│   └── trace_writer.py         # trace JSON、ROI PGM 图、缺陷 overlay PPM 写入
└── tests/                      # 协议、配方、质量门禁、ROI、模型、融合、trace、IPC 安全和架构就绪度测试
```

根目录 `training_tools/` 不是在线检测包的一部分，当前包含：

```text
training_tools/
├── collect_shm_dataset.py      # 复用 ShmClient/算法/trace，从共享内存多光源图生成 raw 图、trace 和训练 manifest
├── collect_trace_dataset.py    # 从 trace 生成训练样本 manifest 和 ROI 图像副本，兼容 pose 目录
├── dataset_manifest.py         # 读取 manifest、PGM ROI 图并按 camera/pose/ROI 聚合多光源训练样本
├── extract_embeddings.py       # 复用在线 FeatureBuilder/EmbeddingExtractor 从真实 ROI 图提取 embedding
├── compute_pca.py              # 从 embedding JSONL 计算 PCA 参数和可选降维 embedding
├── train_patchcore_assets.py   # 串联 embedding、PCA、PatchCore memory bank 和可选 FAISS 索引
├── build_faiss_index.py        # 从 PatchCore memory bank 构建 FAISS 索引
├── evaluate_pipeline.py        # 用 manifest 标注和真实 ROI 图评估当前配方模型
├── train_roi_yolo.py           # 训练 Dome ROI YOLO 并导出 ONNX
├── train_supervised_yolo.py    # 训练已知缺陷监督 YOLO 并导出 ONNX
├── export_wideresnet_embedding.py # 导出 PatchCore 所需 WideResNet50 embedding ONNX
├── replay_dataset.py           # 调用检测流水线做模拟回放
├── benchmark_pipeline.py       # 检测流水线耗时统计和阈值失败
├── build_patchcore_memory_bank.py # 从 JSONL embedding 构建 PatchCore memory bank
├── job_fixture.py              # 离线测试和回放使用的模拟 SeatInspectionJob
└── pipeline_report.py          # 回放和 benchmark 报告格式化
```

根目录 `tools/validate_architecture_readiness.py` 用于把 V4/PPT 架构要求固化成静态检查项：

- `--scope reference` 校验参考实现是否具备固定机位、机器人飞拍、共享内存 v2、质量门禁、trace、ROI/ECC/ONNX/PatchCore/FAISS 接入点等能力。
- `--scope production` 校验上线阻塞项，真实模型资产、固定双机位正式生产配置仍是占位值或固定机位光源/生产配方不一致时会返回 `BLOCKED`。

根目录 `tools/validate_deployment_preflight.py` 用于 Windows 工控机上机前交接：

- 默认模式确认当前环境可实现的参考链路、Windows 共享内存映射、跨平台 IPC 入口、部署包入口和 PLC 前手动联调路径无本地阻塞。
- `--strict-production` 用于放行前，把固定双机位正式生产配置、生产光源配方对齐和真实模型资产缺失升级为 `BLOCKED`。
- 真实模型、现场 `production.conf`、MES/报警/监控协议属于现场 ACTION，不应在本机用占位产物伪造通过。

## 关键实现说明

### 公开入口

- `SeatSurfaceAoiAlgorithm`：纯算法入口。输入 `SeatInspectionJob`，按 `recipe_id` 加载配方，调用 `InspectionPipeline`，可选写 trace，返回 `AlgorithmRun`。
- `InspectionPipeline`：测试和扩展时最常用的编排类。构造函数允许注入质量门禁、预处理、特征、推理、融合、规则引擎等子模块。
- `DetectorProcess`：在线进程入口。只负责初始化 `ShmClient`、等待任务、调用算法 facade、发布结果和释放共享内存 slot。
- `DisplayChannelWriter`：只读前端展示通道输出器。detector 成功写回共享内存后追加 `display_events.jsonl` 并原子更新 `display_latest.json`，供 PySide6/QML 前端读取。

### 前端展示通道

`python_detector.detector_main` 默认启用展示通道，输出目录为 C++ 运行配置里的 `trace_root`；也可以通过 `--display-root` 覆盖，或用 `--disable-display-channel` 关闭。

```bash
uv run python -m python_detector.detector_main \
  --config cpp_controller/config/station_runtime.example.conf \
  --display-root trace \
  --once --timeout-ms 8000
```

输出文件：

- `display_latest.json`：最近一次 Python detector 判定，原子替换，适合 PySide6/QML 轮询。
- `display_events.jsonl`：检测结果追加日志，适合前端日志页或回放。

事件字段包含 `sequence_id`、`trigger_id`、`seat_id`、`sku`、`recipe_id`、`decision`、`quality_pass`、`error_code`、`elapsed_ms`、缺陷列表、质量/错误消息、`trace_dir`、原始采集 PGM 图、ROI PGM 图和 overlay PPM 图路径。展示通道由本仓库 `display_app/` 的 PySide6/QML 前端只读消费，也可供外部 `online-detection-app` 对接；它不读写现有 C++/Python 共享内存 slot。如果展示 JSON 落盘失败，只打印告警，不改变已写回 C++ 的检测结果。采集失败、detector timeout 等 C++ 侧保守结果后续可由前端读取 `trace_root/cpp_controller_events.jsonl` 补充显示。

当 ONNX 模型文件不存在、仍是 1 字节占位文件、ONNX/numpy 依赖缺失、PCA 参数或 PatchCore memory bank 未就绪时，pipeline 会返回 `RECHECK` + `CONFIGURATION_ERROR`，并在 `error.json` 中写入 `asset_unavailable=true` 和具体资产路径。这类状态表示“当前没有足够模型能力判定”，不会输出 `OK`，也不会直接输出 `NG`；trace 会保存 `raw_images/` 原始采集图，前端可直接显示，后续训练工具可继续从 trace/manifest 生成训练样本。

当前仓库已内置 `display_app/` 作为展示通道消费方，迁移并收敛了 `/Users/yyh/code/online-detection-app` 的 PySide6/QML 监控页面。它只轮询 `display_latest.json`、读取 trace PGM/PPM 图像并更新 QML ViewModel，不启动原项目的相机、PLC、触发服务、模型部署或 `seat_defect_core`。

```bash
uv sync --extra display
uv run seat-aoi-display --trace-root trace --line-id AOI-1
```

### 配方与标定

`RecipeManager` 默认从包内 `python_detector/config` 加载 YAML，不依赖当前工作目录。`CalibrationManager` 通过 `paths.resolve_package_path()` 同时兼容包内路径和历史仓库相对路径，例如 `python_detector/config/roi/default_roi.yaml`。

配方中的 `cameras` 实际表示检测视角配置。固定机位模式下 `pose_id` 默认等于 `camera_id`；如果某个固定机位只配置默认视角，Python 检测层允许同一 `camera_id` 下动态 `pose_id` 的多张照片复用该机位的标定、ROI 和模型配置，并在特征、结果和 trace 中继续保留原始 `pose_id`。机器人飞拍模式下允许多个视角共享同一 `camera_id`，并用显式 `pose_id` 区分轨迹点、ROI、标定和模型配置，例如 `EYE_IN_HAND/T1_BACKREST`、`EYE_IN_HAND/T2_CUSHION`；这类显式 pose 配方不会把未知 `pose_id` fallback 到第一条配置。`cameras` 支持字典和列表两种写法；列表写法会按条目保序解析，不会再把相同 `camera_id` 的不同 `pose_id` 折叠覆盖，重复 `(camera_id, pose_id)` 会报配方校验错误。

模型补齐后，固定机位生产任务应使用 `recipe_id=seat_a_black_leather_production_v1`，机器人飞拍生产任务应使用 `recipe_id=seat_a_robot_flyshot_production_v1`。这两个配方会启用 `onnx_yolo` Dome ROI、`ecc` 多光源配准、监督 ONNX 主检测、WideResNet50 embedding、PCA、PatchCore KNN 和可选 FAISS safety net；仓库内生产标定和 `production_full_roi.yaml` 是可校验模板，真实上线必须替换为现场标定和 ROI。

当前固定机位 C++ 生产配置是双相机 + 3 光源 `light_order=1,2,3`，映射到 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT`。`production_recipe.yaml` 已同步为三光源生产配方，模型输入通道为 `ch0_diffuse/ch1_polar_diffuse/ch2_high_left`；若未来补第 4 路 `HIGH_RIGHT`，必须同步生产配方、模型输入通道、训练资产和测试。

配方 schema 会校验：

- `light_order` 与 `quality.required_lights` 一致性。
- V4 语义光源到真实光源的映射。
- ROI 定位、配准基准光源和 fallback 光源合法性。
- 模型引用存在，且 primary / safety_net 角色不能混用。
- 模型输入通道、类别列表、阈值、bbox 格式和输出 decode 规则。
- PatchCore `faiss_index_path` 可选；真实产物放在根目录 `model/`，上线前用 `tools.validate_model_assets` 检查占位文件是否已替换。

### IPC 与协议

`ipc/shm_protocol.py` 必须与 `cpp_controller/include/ipc/shm_protocol.hpp` 保持二进制布局一致。当前协议为 v2，每帧携带 `camera_id`、`pose_id`、`shot_id`、机器人时间戳和 TCP 位姿；Python 按 `(camera_id, pose_id)` 组包为 `CameraBundle`。`tools.validate_protocol` 用于校验 Python 结构体大小；修改协议时必须同步：

- C++ 协议结构体和 ring buffer。
- Python `shm_protocol.py`、`shm_client.py`、`data_types.py`。
- `tools.validate_protocol` 和相关测试。
- `docs/shm_protocol.md`、README 和本文。

`ShmClient` 对共享内存输入执行 header CRC、payload CRC、slot 状态、序列号、payload 边界、图像区域下界、图像 range 重叠、重复 camera/pose/light 等安全校验。解析失败会发布保守错误结果并释放输入 slot，图像偏移指向元数据区、图像大小小于 stride x height、越界或多图重叠都会返回 `INVALID_PAYLOAD`，CRC 不匹配返回 `CRC_MISMATCH`。解析成功时会记录当前任务中的 `camera_id/pose_id -> camera_index` 动态映射，确保机器人飞拍末端相机（例如 `EYE_IN_HAND`）在 NG/RECHECK 缺陷结果回写时也能使用正确的 `camera_index`，不依赖固定静态表。`ErrorCode` 与 C++ `common/error_code.hpp` 保持枚举值一致，当前包含 `LIGHT_FAULT`、`CAMERA_FAULT`、`TRIGGER_SYNC_FAULT`、`CONFIGURATION_ERROR` 和 `ROBOT_FAULT` 等 C++ 采集侧结构化失败码。

### 质量门禁与预处理

`ImageQualityGate` 在进入模型前拦截不可靠输入，包括缺少必需机位/光源、未启用的显式机器人 pose、非单调时间戳、重复帧号、曝光/增益漂移、过曝欠曝、锐度不足、光源亮度漂移，以及同一视角必需光源间的 `shot_id`、机器人时间戳、TCP 坐标和 RPY 姿态不一致。固定机位默认配置可接收同一机位的动态 `pose_id`，但仍要求每个动态视角自己的必需光源完整、时序一致、质量通过；固定机位可以保留空的机器人字段，一旦任一光源携带机器人字段，其余必需光源必须保持一致。失败结果进入 `RuleEngine.make_quality_fail_result()`，不会输出 `OK`。

`Preprocessor` 只接受当前实现支持的 `MONO8` / `UINT8` / 单通道图像，并显式检查 stride、图像长度、标定版本和图像尺寸。ROI 可以是轴对齐矩形裁剪，也可以是四点透视展开。Dome ROI 定位会按 `roi_name` 聚合同名候选，优先选择置信度最高、姿态误差最低的候选；同名 ROI 出现互相冲突的框时返回 `RECHECK`，避免重复检测静默覆盖 ROI。

### 多光源特征

`ReflectanceCubeBuilder` 将同一 ROI 下多个光源图组织成 cube。`fixed_calibration` 模式检查标定矩阵角点误差；`ecc` 模式以 `base_light_id` ROI 为基准，对其余光源做整数像素平移搜索，记录相关系数、位移和误差，并在配准通过时把非基准光源 ROI 重采样到基准坐标后再进入特征构建。配准失败、相关性不足或位移超过阈值时仍走质量失败结果，不输出 `OK`。

`FeatureBuilder` 构建当前标准通道：

- `ch0_diffuse`
- `ch1_polar_diffuse`
- `ch2_high_left`
- 参考/扩展 4 光源方案可额外使用 `ch3_high_right` 和 `ch4_high_max_min`

可选增强包括低角度暗场差分、局部对比和高光抑制。模型输入 tensor 使用 `NCHW`，并保留 `evidence_lights_by_channel` 供结果回写和 trace 使用。

### 模型后端

`InferenceEngine` 通过 `ModelRegistry` 缓存后端实例，缓存 key 包含后端、路径、fake 模式、模型族、角色、输入通道、类别列表、decode、bbox 格式、阈值、embedding/PCA/PatchCore 参数等关键配置，避免不同配方误复用模型。

当前后端：

- `fake`：测试和模拟链路默认后端。
- `onnx`：可选 ONNX detection rows 后端，要求 `onnxruntime` 和 `numpy`。
- `patchcore_knn`：PatchCore safety net，使用 statistical 或 ONNX embedding、可选 PCA、memory bank；配置 `faiss_index_path` 时优先尝试 FAISS，失败时回退 exact KNN，并在 `anomaly_summary` 写入实际 backend 和 fallback reason。

模型资产缺失、占位文件未替换、后端依赖缺失、PCA 参数或 PatchCore memory bank 未就绪会抛出 `ModelAssetUnavailableError`，由 pipeline 转成 `RECHECK` + `CONFIGURATION_ERROR`，并写入 `sample_collection.reason=model_asset_unavailable`。模型已经加载但输出为空、bbox 越界、class id 错误或维度不匹配仍按模型运行异常处理，不能静默降级为 `OK`。

离线训练工具复用同一套模型输入契约：

- `training_tools.dataset_manifest` 读取 `dataset_manifest.jsonl` 和 ROI `P5` PGM 图，将同一 trace/camera/pose/ROI 下的多光源样本聚合；旧 manifest 没有 `pose_id` 时默认回退到 `camera_id`。
- `training_tools.extract_embeddings` 调用在线 `FeatureBuilder` 和 `EmbeddingExtractor`，确保训练出的 PCA/PatchCore 资产与在线 `NCHW` 输入通道一致。
- `training_tools.evaluate_pipeline` 调用在线 `InferenceEngine`，按 manifest 中的人工标注或弱标签计算整体、类别、ROI、camera 和 split 指标。
- `training_tools.collect_trace_dataset` 同时兼容旧 trace 目录 `images/<camera>/<roi>/<light>.pgm` 和新目录 `images/<camera>/<pose>/<roi>/<light>.pgm`，生成的样本路径与 manifest 都包含 `pose_id`，避免机器人飞拍同一末端相机下的不同 pose 互相覆盖。
- `training_tools.collect_shm_dataset` 调用在线 `ShmClient`、`SeatSurfaceAoiAlgorithm` 和 `TraceWriter`，从 C++ 共享内存任务获取多相机多光源图像，保存按 `camera_id/pose_id` 分目录的 `raw_images/`、`raw_frame_manifest.jsonl`，并生成 trace/训练 manifest；它不控制 PLC、相机或频闪。
- `training_tools.train_patchcore_assets` 训练 PatchCore safety net 所需的 embedding/PCA/memory bank/FAISS 资产；`training_tools.export_wideresnet_embedding` 生成生产配方引用的 WideResNet50 embedding ONNX。

### 融合、规则和追溯

`FusionEngine` 对同一 camera/pose/ROI/class 执行 IoU NMS，合并 evidence lights，并限制每个 ROI 候选数。NMS 抑制数和候选容量溢出数分开统计；如果容量溢出隐藏了候选且规则原本会输出 `OK`，`RuleEngine` 会改为 `RECHECK` 并写入 `CONFIGURATION_ERROR`，避免用 `OK` 掩盖算法不确定性。`DefectFilter` 和 `RuleEngine` 根据类别阈值、面积阈值和候选分数输出 `OK`、`NG`、`RECHECK` 或 `ERROR`。

`TraceWriter` 按配方 trace 策略保存：

- `job.json`
- `result.json`
- `quality_report.json`
- `roi_location_report.json`
- `registration_report.json`
- `feature_summary.json`
- `fusion_summary.json`
- `timings.json`
- `error.json`
- 原始采集 PGM 图、ROI PGM 图和缺陷 overlay PPM 图；原始图路径为 `raw_images/<camera_id>/<pose_id>/<light_id>.pgm`，ROI 图路径为 `images/<camera_id>/<pose_id>/<roi_name>/<light_id>.pgm`，overlay 文件名也包含 `pose_id`。

## 扩展规则

新增能力时优先按层放置：

- 新增配方字段：改 `config/recipe_schema.py`、默认配方、测试和本文。
- 新增标定或 ROI 格式：改 `config/calibration_manager.py`、`pipeline/preprocessor.py`、测试和本文。
- 新增质量门禁：改 `pipeline/quality_gate.py` 和对应测试。
- 新增特征：改 `pipeline/feature_builder.py`，明确 feature 名、输入光源和 evidence lights。
- 新增模型后端：改 `models/inference_engine.py` 或拆分新后端模块，保持 `ModelBackend.run()` 统一接口。
- 新增真实模型产物：放入根目录 `model/`，同步 `model/README.md`、生产配方模板和 `tools.validate_model_assets`。
- 新增 trace 字段：改 `trace/trace_writer.py`、测试和本文。
- 新增离线样本或训练支撑能力：放入根目录 `training_tools/`，只能消费 `python_detector` 公开入口和 trace 产物。
- 新增回放、benchmark、训练或模型资产生成入口：放入 `training_tools/`，不要在 `tools/` 增加转发包装。
- 新增或调整项目级协议、模型资产、架构就绪度或 IPC 联调校验：放入 `tools/`，同步 `tools.validate_architecture_readiness`、相关测试、根 README 和本文。
- 修改在线共享内存协议：必须同步 C++、Python、校验工具、协议文档和测试。

## 验证命令

```bash
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
uv run python -m tools.validate_architecture_readiness --scope reference
uv run python -m tools.validate_deployment_preflight
uv run python -m training_tools.replay_dataset --count 3 --write-trace
bash tools/run_simulated_ipc.sh
bash tools/run_simulated_ipc.sh --config cpp_controller/config/station_runtime.robot_flyshot.example.conf
```

有 trace 数据后再执行离线样本生成：

```bash
uv run python -m training_tools.collect_trace_dataset --trace-root trace --output datasets/seat_trace_v1
uv run python -m training_tools.collect_shm_dataset --output datasets/seat_shm_v1 --max-jobs 10 --trace-root trace/training_shm
uv run python -m training_tools.export_wideresnet_embedding --output model/wideresnet50/seat_wrn50_embedding.onnx --embedding-dim 1024
uv run python -m training_tools.extract_embeddings --manifest datasets/seat_trace_v1/dataset_manifest.jsonl --output datasets/seat_trace_v1/embeddings.jsonl --backend statistical
uv run python -m training_tools.train_patchcore_assets --manifest datasets/seat_trace_v1/dataset_manifest.jsonl --output-dir model/patchcore --split train --pca-components 3 --coreset-ratio 0.1
uv run python -m training_tools.evaluate_pipeline --manifest datasets/seat_trace_v1/dataset_manifest.jsonl --output reports/evaluation_report.json --split test
uv run python -m training_tools.train_roi_yolo --data datasets/roi_yolo/dataset.yaml --output model/roi_yolo/seat_roi_yolo.onnx
uv run python -m training_tools.train_supervised_yolo --data datasets/supervised_defect_yolo/dataset.yaml --output model/supervised_defect/seat_defect_detector.onnx
```

涉及 ONNX 后端时：

```bash
uv sync --group dev --extra onnx
uv run pytest python_detector/tests/test_model_backend.py
```

涉及 FAISS 加速时：

```bash
uv sync --group dev --extra onnx --extra faiss
uv run pytest python_detector/tests/test_v4_alignment_modules.py
```
