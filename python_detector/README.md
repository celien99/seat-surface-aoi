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

离线训练样本生成、真实 ROI 图 embedding 提取、PCA/PatchCore/FAISS 无监督资产训练、ROI YOLO ONNX 导出、manifest 评估、回放和 benchmark 放在根目录 `training_tools/`。调用方向只能是 `training_tools -> python_detector`，在线算法层不能 import 离线训练工具；训练、回放和 benchmark 不再通过 `tools/` 暴露兼容入口。监督缺陷 YOLO 训练脚本仅作为离线研究工具保留，不属于当前生产检测链路依赖。

## 依赖管理

Python 层使用项目根目录的 `pyproject.toml` 和 `uv.lock` 管理依赖：

- 默认运行依赖：`PyYAML`、`numpy`；`PyYAML` 用于配方、标定和 ROI YAML，`numpy` 用于在线图像质量门禁、预处理、ROI 定位、配准、特征构建和模型输入/输出数组处理。
- `test` dependency group：`pytest`、`numpy`，用于单元测试和 ONNX 输出解析测试。
- `dev` dependency group：`pytest`、`numpy`、`ruff`，用于开发验证。
- `training` dependency group：`torch`、`torchvision`、`onnx`、`onnxscript`、`onnxruntime`、`ultralytics`、`faiss-cpu`，用于 ROI YOLO、WideResNet50 embedding、PCA/PatchCore/FAISS 资产训练和导出。
- `onnx` extra：`numpy`、`onnxruntime`，仅在启用 YOLO ROI、WideResNet50 embedding 或可选 ONNX detection 实验后端时需要。
- `faiss` extra：`faiss-cpu`、`numpy`，仅在 PatchCore 启用 FAISS 索引加速时需要；未安装或索引缺失时回退 exact KNN。

常用命令：

```powershell
uv sync --group dev
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
uv run python -m tools.validate_architecture_readiness --scope reference
uv run python -m tools.validate_deployment_preflight
uv run python -m python_detector.detector_main --once --timeout-ms 8000
uv run python -m python_detector.detector_main `
  --config cpp_controller/config/station_runtime.test.conf `
  --once --timeout-ms 8000
```

`tools.validate_model_assets` 会校验 ONNX/PCA/bank/FAISS 是否存在且不是占位文件，并检查当前 PatchCore 链路的维度一致性：配方 `embedding_dim`、PCA 输入维度、PCA 输出维度、memory bank 维度和 FAISS 维度/向量数必须对齐。

端到端模拟使用 `uv run python tools/run_simulated_ipc.py`。该入口会先启动 C++ 主控，再启动 detector 读取共享内存任务并写回结果。带 `--config` 运行时，脚本会把同一份 C++ 运行配置传给 detector，detector 会读取 `slot_count`、`frame_slot_size` 和 `result_slot_size`，确保 4096 x 3072 固定机位高分辨率图像不会因为 Python 仍使用默认 16 MB slot 而布局不匹配。`--replay-capture` 或 `--config cpp_controller/config/station_runtime.replay_capture.conf` 会走 `images_capture` 真实 PNG 共享内存回放：C++ simulated camera 随机抽完整两机位三光源样本写入 Frame SHM，Python detector 从 SHM 读取并按生产配方检测。该回放不是 Python-only 离线模拟；文件名时间戳只用于 C++ 分组排序，Python 看到的是本次在线模拟采集 metadata。若当前 PNG 内容触发质量门禁或模型保守规则，结果仍会返回 `RECHECK/ERROR`；OK 件 trace 是否落盘由生产配方 `trace.save_ok_ratio` 决定。`python_detector/tests/test_run_simulated_ipc_tool.py` 固化了 Windows 入口的生成器选择、直接编译回退参数、配置超时传递、replay 快捷入口，以及仓库搬迁后旧 CMake 缓存的定向清理行为，避免 CMake 默认选中缺失的 `nmake.exe` 或复用旧绝对路径后提前失败。

## 部署打包

根目录 `tools/package_release.py` 会把 Python 在线检测层和模型目录一起放入离线部署包。包内 Python 相关内容包括：

- `python_detector/`：在线 detector、IPC 客户端、配方、标定、ROI 模板、算法流水线、模型后端和测试。
- `display_app/`：PySide6/QML 展示前端，读取 detector display 通道；运行前需要安装 `display` extra。
- `training_tools/`：离线回放、benchmark、embedding、PCA/PatchCore/FAISS 资产生成工具。
- `model/`：默认集成根目录 `model/`；生产打包前必须先把真实模型产物替换到该目录。
- `pyproject.toml` 和 `uv.lock`：用于在有网目标环境恢复 Python detector 依赖。
- `tools/package_python_offline_deps.py`：用于工控机无公网时生成 wheelhouse、项目 wheel 和离线安装脚本。

参考联调包可以生成但不代表生产模型就绪：

```powershell
uv run python -m tools.package_release
```

生产包必须先替换根目录 `model/` 下的 1 字节占位 ONNX/PCA/FAISS 文件，然后直接打包：

```powershell
uv run python -m tools.package_release
```

工控机无公网时，需要在有网且与工控机平台一致的机器上额外生成 Python 离线依赖包：

```powershell
uv run python -m tools.package_python_offline_deps --extra display --extra onnx --extra faiss
```

把项目部署包和 `dist/*python-offline-deps*.zip` 一起拷到工控机。解包后在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\offline_python_deps\install_offline.ps1 -ProjectRoot .
.\.venv\Scripts\python.exe -m tools.validate_protocol
.\.venv\Scripts\python.exe -m tools.validate_deployment_preflight
```

离线依赖包通过本地 `wheelhouse/` 在工控机重新创建 `.venv`，不要复制开发机现有 `.venv/`。如果现场只跑 detector，可去掉不需要的 `--extra`；如果需要训练工具，单独生成并验证包含 `--group training` 的离线包。

解包后可运行：

```powershell
uv run python validate_package.py
uv run python run_packaged_simulated_ipc.py
```

打包不会包含现场 `trace/`、训练数据集、日志、`.venv`、Python wheel 缓存或本地缓存。Python detector 仍只负责检测链路，不控制 PLC、相机、机器人或频闪。

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
│   ├── schema_types.py         # 配方 frozen dataclass 定义（Recipe、ModelConfig、QualityConfig 等 17 个类型）
│   ├── recipe_schema.py        # 配方 YAML 加载、字段解析（_*_from_dict）和 RecipeManager
│   ├── schema_validators.py    # 配方校验函数和类型检查原语（_validate_*、_str、_float 等）
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
│   ├── roi_locator.py          # Dome 语义光源 ROI 定位，支持 template/fake_yolo/onnx_yolo/onnx_yolo_seg
│   ├── reflectance_cube.py     # 多光源 ROI 对齐后的 ReflectanceCube 构建
│   ├── ecc_registration.py     # ECC 风格平移搜索和非基准光源 ROI 重采样
│   ├── feature_builder.py      # 多光源特征和 NCHW tensor 构建
│   ├── fusion_engine.py        # 候选框融合、NMS、候选数量限制
│   ├── defect_filter.py        # 单一判定阈值、面积阈值和长宽比过滤
│   └── rule_engine.py          # OK/NG/RECHECK/ERROR 规则判定
├── models/
│   ├── inference_engine.py     # Fake/ONNX/PatchCore 后端统一推理入口、空间 anomaly map 校验和模型缓存
│   ├── onnx_runtime.py         # ONNX Runtime session、numpy 输入和统一保守错误包装
│   ├── embedding.py            # statistical 与 onnx_wideresnet50 embedding 入口
│   ├── pca.py                  # PCA JSON 参数加载、版本校验和投影
│   └── patchcore.py            # PatchCore memory bank exact KNN 参考实现
├── trace/
│   └── trace_writer.py         # trace JSON、raw/ROI PNG 图、raw 原图尺寸检测 overlay PNG 写入
└── tests/                      # 协议、配方、质量门禁、ROI、模型、融合、trace、IPC 安全和架构就绪度测试
```

根目录 `training_tools/` 不是在线检测包的一部分，当前包含：

```text
training_tools/
├── collect_shm_dataset.py      # 复用 ShmClient/算法/trace，从共享内存多光源图生成 raw 图、trace 和训练 manifest
├── collect_trace_dataset.py    # 从 trace 生成训练样本 manifest 和 ROI 图像副本，兼容 pose 目录
├── collect_capture_dataset.py  # 从 capture_only 平铺 PNG 目录调用 ROI 模型生成 ROI PNG manifest
├── dataset_manifest.py         # 读取 manifest、PNG ROI 图并按 camera/pose/ROI 聚合多光源训练样本
├── extract_embeddings.py       # 复用在线 FeatureBuilder/EmbeddingExtractor 从真实 ROI 图提取 embedding
├── compute_pca.py              # 从 embedding JSONL 计算 PCA 参数和可选降维 embedding
├── train_patchcore_assets.py   # 串联 embedding、PCA、PatchCore memory bank 和可选 FAISS 索引
├── build_faiss_index.py        # 从 PatchCore memory bank 构建 FAISS 索引
├── evaluate_pipeline.py        # 用 manifest 标注和真实 ROI 图评估当前配方模型
├── simulate_capture_detection.py # Python-only 从 images_capture 样本模拟检测链路并生成检测图
├── train_roi_yolo.py           # 训练 Dome ROI YOLO segmentation 或 bbox 模型并导出 ONNX
├── train_supervised_yolo.py    # 可选离线研究工具，生产配方不依赖监督缺陷 YOLO
├── export_wideresnet_embedding.py # 导出 PatchCore 所需 WideResNet50 embedding ONNX
├── replay_dataset.py           # 调用检测流水线做模拟回放
├── benchmark_pipeline.py       # 检测流水线耗时统计和阈值失败
├── build_patchcore_memory_bank.py # 从 JSONL embedding 构建 PatchCore memory bank
├── job_fixture.py              # 离线测试和回放使用的模拟 SeatInspectionJob
└── pipeline_report.py          # 回放和 benchmark 报告格式化
```

ROI 定位当前统一映射到 `seat`。`roi_locator.class_names`、ROI 模板、标定文件、trace 目录和训练用 YOLO segmentation 数据集都应使用该 ROI 名称；`production_full_roi.yaml` 的文件名仅表示全座椅安全边界模板，不代表缺陷类别名。

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
- `DisplayChannelWriter`：只读前端展示通道输出器。detector 成功写回共享内存后追加 `display_events.jsonl` 并原子更新 `display_latest.json`，供 PySide6/QML 前端读取；模型资产未就绪时同步输出 `sample_collection`，用于前端显示采样模式。

### 前端展示通道

`python_detector.detector_main` 默认启用展示通道，输出目录为 C++ 运行配置里的 `trace_root`；也可以通过 `--display-root` 覆盖，或用 `--disable-display-channel` 关闭。

```powershell
uv run python -m python_detector.detector_main `
  --config cpp_controller/config/station_runtime.test.conf `
  --display-root trace `
  --once --timeout-ms 8000
```

输出文件：

- `display_latest.json`：最近一次 Python detector 判定，原子替换，适合 PySide6/QML 轮询。
- `display_events.jsonl`：检测结果追加日志，适合前端日志页或回放。

事件字段包含 `sequence_id`、`trigger_id`、`seat_id`、`sku`、`recipe_id`、`decision`、`quality_pass`、`error_code`、`elapsed_ms`、缺陷列表、质量/错误消息、`sample_collection`、`trace_dir`、原始采集 PNG 图、ROI PNG 图和检测 overlay PNG 图路径。展示通道由本仓库 `display_app/` 的 PySide6/QML 前端只读消费，也可供外部 `online-detection-app` 对接；它不读写现有 C++/Python 共享内存 slot。如果展示 JSON 落盘失败，只打印告警，不改变已写回 C++ 的检测结果。检测 trace、原始采集图、ROI 图或 overlay 写入失败会在写回共享内存前把当前件改为 `RECHECK/DEVICE_FAULT`，避免磁盘异常时继续输出 `OK`。采集失败、detector timeout 等 C++ 侧保守结果可由前端读取 `trace_root/cpp_controller_events.jsonl` 补充显示。

当 ONNX 模型文件不存在、仍是 1 字节占位文件、ONNX/numpy 依赖缺失、PCA 参数或 PatchCore memory bank 未就绪时，pipeline 会返回 `RECHECK` + `CONFIGURATION_ERROR`，并在 `error.json` 中写入 `asset_unavailable=true` 和具体资产路径。这类状态表示“当前没有足够模型能力判定”，不会输出 `OK`，也不会直接输出 `NG`；trace 会保存 `raw_images/` 原始采集图，前端可直接显示，后续训练工具可继续从 trace/manifest 生成训练样本。

当前仓库已内置 `display_app/` 作为展示通道消费方，迁移并收敛了 `/Users/yyh/code/online-detection-app` 的 PySide6/QML 监控页面。它轮询 `display_latest.json`、读取 C++ 主控事件、读取 trace PNG 图像并更新 QML ViewModel，不启动原项目的相机、PLC、触发服务、模型部署或 `seat_defect_core`。前端会持久化 `display_operator_events.jsonl` 和 `display_review_queue.json`，记录操作员复核动作。

```powershell
uv sync --extra display
uv run seat-aoi-display --trace-root trace --line-id AOI-1
```

### 配方与标定

`RecipeManager` 默认从包内 `python_detector/config` 加载 YAML，不依赖当前工作目录。`CalibrationManager` 通过 `paths.resolve_package_path()` 同时兼容包内路径和历史仓库相对路径，例如 `python_detector/config/roi/default_roi.yaml`。

配方中的 `camera_defaults` 用于声明同一 SKU 下各检测视角共享的模型、ROI 模板、光源顺序、基准光源和 ROI 级模型映射；`cameras` 实际表示检测视角配置，只需要写差异字段，例如 `camera_id`、`pose_id` 和 `calibration_id`。固定机位模式下 `pose_id` 默认等于 `camera_id`；如果某个固定机位只配置默认视角，Python 检测层允许同一 `camera_id` 下动态 `pose_id` 的多张照片复用该机位的标定、ROI 和模型配置，并在特征、结果和 trace 中继续保留原始 `pose_id`。机器人飞拍模式下允许多个视角共享同一 `camera_id`，并用显式 `pose_id` 区分轨迹点、ROI、标定和模型配置，例如 `EYE_IN_HAND/T1_BACKREST`、`EYE_IN_HAND/T2_CUSHION`；这类显式 pose 配方不会把未知 `pose_id` fallback 到第一条配置。`cameras` 支持字典和列表两种写法；列表写法会按条目保序解析，不会再把相同 `camera_id` 的不同 `pose_id` 折叠覆盖，重复 `(camera_id, pose_id)` 会报配方校验错误。

相机条目仍可覆盖 `camera_defaults` 中的任一字段，兼容旧配方；但内置配方只在 `cameras` 中保留真正不同的标定或 pose。像素尺寸、图像尺寸和多光源几何对齐属于标定事实，优先放在 `calibration/*.yaml` 中维护，不在配方里重复写 `pixel_size_mm`。

模型补齐后，固定机位生产任务应使用 `recipe_id=seat_a_black_leather_production_v1`，机器人飞拍生产任务应使用 `recipe_id=seat_a_robot_flyshot_production_v1`。这两个配方会启用 `onnx_yolo_seg` Dome ROI segmentation、`ecc` 多光源配准、WideResNet50 embedding、PCA、PatchCore KNN 和可选 FAISS 无监督异常检测主模型；仓库内生产标定和 `production_full_roi.yaml` 是可校验模板，其中 ROI `polygon_xy` 作为安全边界和 `output_size` 约束，真实 ROI polygon 由 segmentation mask 在线生成。

当前固定机位 C++ 生产配置是双相机 + FL-ACDH 三路共享频闪光源，采集顺序为 `light_order=1,2,3`，映射到 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT`。`production_recipe.yaml` 顶层 `light_order`、`camera_defaults.light_order`、`quality.required_lights` 和模型输入通道均保持这三路检测光源一致；DOME 语义暂时映射到 `DIFFUSE`，仅为 ROI 定位后端提供输入图，不额外要求 C++ 发布 `DOME_ROI` 采集轮次。若未来补常亮 Dome ROI 或新增其它检测光源，必须同步 C++ 配置、生产配方、模型输入通道、训练资产和测试；Python 特征构建和训练工具按配方通道声明处理，不把三路作为算法常量。

配方 schema 会校验：

- 顶层 `light_order` 与 `quality.required_lights` 一致性；当前生产配方与 C++ 三路频闪顺序完全一致。
- V4 语义光源到真实光源的映射。
- `camera_defaults` 与每个 `cameras` 视角合并后的模型、ROI 模板、标定和光源字段合法性。
- ROI 定位、配准基准光源和 fallback 光源合法性。
- 模型引用存在，且 primary / safety_net 角色不能混用。
- 模型输入通道、单一 `decision_threshold`、bbox 格式和输出 decode 规则。
- PatchCore `faiss_index_path` 可选；真实产物放在根目录 `model/`，上线前用 `tools.validate_model_assets` 检查占位文件是否已替换。

### IPC 与协议

`ipc/shm_protocol.py` 必须与 `cpp_controller/include/ipc/shm_protocol.hpp` 保持二进制布局一致。当前协议为 v2，每帧携带 `camera_id`、`pose_id`、`shot_id`、机器人时间戳和 TCP 位姿；Python 按 `(camera_id, pose_id)` 组包为 `CameraBundle`。`tools.validate_protocol` 用于校验 Python 结构体大小；修改协议时必须同步：

- C++ 协议结构体和 ring buffer。
- Python `shm_protocol.py`、`shm_client.py`、`data_types.py`。
- `tools.validate_protocol` 和相关测试。
- `docs/shm_protocol.md`、README 和本文。

`ShmClient` 对共享内存输入执行 header CRC、payload CRC、slot 状态、序列号、payload 边界、图像区域下界、图像 range 重叠、重复 camera/pose/light 等安全校验。解析失败会发布保守错误结果并释放输入 slot，图像偏移指向元数据区、图像大小小于 stride x height、越界或多图重叠都会返回 `INVALID_PAYLOAD`，CRC 不匹配返回 `CRC_MISMATCH`。解析成功时会记录当前任务中的 `camera_id/pose_id -> camera_index` 动态映射，确保机器人飞拍末端相机（例如 `EYE_IN_HAND`）在 NG/RECHECK 缺陷结果回写时也能使用正确的 `camera_index`，不依赖固定静态表。`ErrorCode` 与 C++ `common/error_code.hpp` 保持枚举值一致，当前包含 `LIGHT_FAULT`、`CAMERA_FAULT`、`TRIGGER_SYNC_FAULT`、`CONFIGURATION_ERROR` 和 `ROBOT_FAULT` 等 C++ 采集侧结构化失败码。

### 质量门禁与预处理

`ImageQualityGate` 在进入模型前拦截不可靠输入，包括缺少必需机位/检测光源、未启用的显式机器人 pose、非单调时间戳、重复帧号、曝光/增益漂移、过曝欠曝比例超限、锐度不足、光源亮度漂移，以及同一视角必需检测光源间的 `shot_id`、机器人时间戳、TCP 坐标和 RPY 姿态不一致。固定机位默认配置可接收同一机位的动态 `pose_id`，但仍要求每个动态视角自己的 `quality.required_lights` 完整、时序一致、质量通过；当前生产配方为 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT` 三路，`light_seq_index` 分别匹配顶层采集顺序 `0/1/2`。生产配方当前将单帧过曝像素比例 `max_saturation_ratio` 和过暗像素比例 `max_dark_ratio` 都配置为 `0.40`，同时保留缺帧、时序、曝光/增益一致性、锐度、运动梯度和配准等硬门禁。亮度统计、过曝/欠曝比例、Laplacian 锐度和运动梯度使用 `numpy` 对有效像素区向量化计算，不再用 Python 逐像素循环处理高分辨率原图。固定机位可以保留空的机器人字段，一旦任一必需检测光源携带机器人字段，其余必需检测光源必须保持一致。失败结果进入 `RuleEngine.make_quality_fail_result()`，不会输出 `OK`。

`Preprocessor` 只接受当前实现支持的 `MONO8` / `UINT8` / 单通道图像，并显式检查 stride、图像长度、标定版本和图像尺寸。ROI 可以是轴对齐矩形裁剪，也可以是四点透视展开；轴对齐矩形裁剪、四点透视双线性采样和 ROI mask 应用使用 `numpy` 切片、网格采样和布尔掩码，避免在高分辨率 raw ROI 上逐像素复制。Dome ROI 定位会按 `roi_name` 聚合同名候选，优先选择置信度最高、姿态误差最低的候选；bbox 后端输出矩形 ROI，seg 后端从 mask 自动生成运行时 `polygon_xy`，当前传给后续配准和模型的是 mask 外接矩形 ROI 图，并会把 mask 外像素置黑，只保留 mask 内目标物体。ROI locator 的模型输入 letterbox、mask bbox/area 统计、mask 裁剪和输出 mask 1px 腐蚀均使用 `numpy` 数组索引、`nonzero/count_nonzero` 和布尔运算处理。同名 ROI 出现互相冲突的框或 mask 时返回 `RECHECK`，避免重复检测静默覆盖 ROI。

### 多光源特征

`ReflectanceCubeBuilder` 将同一 ROI 下多个检测光源图组织成 cube，并按当前视角的 `light_order` 保留可用检测光源。固定机位生产配方当前只使用 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT` 三路；DOME 语义映射到 `DIFFUSE` 只影响 ROI 定位输入选择，不新增特征通道。`fixed_calibration` 模式检查标定矩阵角点误差；`ecc` 模式以 `base_light_id` ROI 为基准，优先使用 OpenCV `findTransformECC` 梯度下降（MOTION_TRANSLATION 模型，~10-20 次迭代，无需穷举搜索）；cv2 不可用或不收敛时自动回退到暴力 NCC 穷举搜索。配准失败、相关性不足或位移超过阈值时仍走质量失败结果，不输出 `OK`。

`FeatureBuilder` 按模型 `input_channels` 惰性构建特征，不隐式固定光源数、通道数或通道序号。未显式声明 `input_channels` 的模型会从配方 `light_order` 生成 `light:<LIGHT_ID>` 通道；生产配方当前显式声明为：

- `light:DIFFUSE`
- `light:POLAR_DIFFUSE`
- `light:HIGH_LEFT`

模型可声明表达式通道，例如 `light:HIGH_RIGHT`、`max_min:HIGH_LEFT:HIGH_RIGHT`、`abs_diff:LOW_LEFT:LOW_RIGHT`、`local_contrast:DIFFUSE`；旧配方中的 `ch0_diffuse`、`ch1_polar_diffuse`、`ch2_high_left`、`ch3_high_right`、`ch4_high_max_min` 仍作为兼容别名解析。缺少未声明光源不会影响当前生产链路。`FeatureBuilder` 使用 `numpy` 数组构建 light、abs_diff、max_min、local_contrast 特征通道，并直接堆叠归一化后的 `NCHW` tensor；模型输入 tensor 保留 `evidence_lights_by_channel` 供结果回写和 trace 使用。

### 模型后端

`InferenceEngine` 通过 `ModelRegistry` 缓存后端实例，缓存 key 包含后端、路径、fake 模式、模型族、角色、输入通道、decode、bbox 格式、阈值、embedding/PCA/PatchCore 参数等关键配置，避免不同配方误复用模型。

当前后端：

- `fake`：测试和模拟链路默认后端。
- `onnx`：可选 ONNX detection rows 后端，要求 `onnxruntime` 和 `numpy`；YOLO detection/segmentation 输出解码使用数组化 class argmax、score filter、bbox 转换和分块 mask logits 计算，并在保留候选前拒绝非有限分数，保持 Python float 阈值边界语义。
- `patchcore_knn`：PatchCore 无监督异常检测主模型或可选安全网，使用 statistical 或 ONNX embedding、可选 PCA、memory bank；配置 `faiss_index_path` 时优先尝试 FAISS，失败时回退 exact KNN，并在 `anomaly_summary` 写入实际 backend 和 fallback reason。

**空间 PatchCore 模式（`spatial_mode: true`）：** 在配方模型配置中启用 `spatial_mode` 后，PatchCore 从"全局嵌入"（整个 ROI → 1 个向量 → 标量分数）切换为"空间嵌入"（ROI → 中间层特征图 → H×W 个 patch 向量 → anomaly_map 热力图）。空间 embedding 保持 ONNX 输出为 `numpy` 数组，使用最近邻索引上采样、通道拼接和 `reshape` 生成 patch 矩阵；PCA 使用批量矩阵乘法投影；PatchCore exact KNN fallback 使用分块矩阵距离和 `partition/sort` 取 top-k，FAISS 后处理直接对距离矩阵做 `sqrt/clip/reshape`。空间模式提供三项关键提升：

1. **像素级缺陷定位**：从 anomaly_map 连通域自动生成缺陷 bbox，不再使用整个 ROI 边界。连通域分析使用 `scipy.ndimage.label` + `find_objects` 向量化实现（替代旧版 Python BFS），异常分数热力图、PCA 投影和 patch embedding 数据流全程保持 `numpy.ndarray`。批量 PCA 会先把输入转换为二维矩阵，并用 `size/ndim/shape` 显式校验空输入和维度，不对 `numpy.ndarray` 做布尔判断。在线推理先把 `anomaly_map` 和 `nearest_distances` 归一化为二维有限矩阵并确认形状一致，再做最大值统计和连通域提取；异常形状或非有限值按模型异常处理，不允许隐式降级为 OK。trace JSON 序列化时再转换为列表。
2. **小缺陷召回率提升**：Global Average Pooling 不再淹没小面积缺陷信号。
3. **检测 overlay**：TraceWriter 自动将 ROI 空间的 anomaly_map 映射回 raw 原图坐标。热力图生成管线使用**双线性插值**（替代最近邻）将 anomaly_map 上采样到 ROI 分辨率以消除块状马赛克效应，叠加**高斯平滑**消除特征提取残余的栅格感，经**形态学闭运算**填补二值掩码内部小孔洞后，仅在最终缺陷候选 bbox 内叠加达到 PatchCore 二值化阈值的黄/红热色，低分正常 ROI 保持灰度原图，同时绘制判定框和缺陷 bbox。`feature_summary.json` 仍保留原始 anomaly_map 数值，overlay 是唯一落盘的检测可视化 PNG。

空间模式要求：`embedding_backend=onnx_wideresnet50`、`spatial_layers` 非空（如 `[layer2, layer3]`），并使用 `--spatial-mode` 重新导出 ONNX 模型和重新训练记忆库。**生产配方默认启用 `spatial_mode: true`**，当前生产配方显式使用 `256x256` 空间 patch 网格，layer2+layer3 原始 patch embedding 为 1536 维，在线先用 `seat_pca.json`（v3, 524维, 95%累积方差）投影，再进入 PatchCore bank/FAISS 评分并生成像素级 anomaly_map 热力图。若需回退到全局嵌入路径（整个 ROI → 1 个向量 → 标量分数），在配方中设置 `spatial_mode: false`。anomaly_map 二值化阈值可通过 `anomaly_binarize_min_ratio`（默认 0.5）和 `anomaly_binarize_relative`（默认 0.3）配置，阈值公式为 `max(score_threshold × min_ratio, max_anomaly × relative)`。

模型资产缺失、占位文件未替换、后端依赖缺失、PCA 参数或 PatchCore memory bank 未就绪会抛出 `ModelAssetUnavailableError`，由 pipeline 转成 `RECHECK` + `CONFIGURATION_ERROR`，并写入 `sample_collection.reason=model_asset_unavailable`。模型已经加载但输出为空、bbox 越界、class id 错误、维度不匹配、空间 anomaly map 非二维或非有限，仍按模型运行异常处理，不能静默降级为 `OK`。

离线训练工具复用同一套模型输入契约：

- `training_tools.dataset_manifest` 读取 `dataset_manifest.jsonl` 和 ROI PNG 图，将同一 trace/camera/pose/ROI 下的多光源样本聚合；旧 manifest 没有 `pose_id` 时默认回退到 `camera_id`。底层解码仍保留历史 PGM 兼容，但当前训练集和新 trace 均生成 PNG。
- `training_tools.extract_embeddings` 调用在线 `FeatureBuilder` 和 `EmbeddingExtractor`，默认使用配方 `models.<key>.input_channels`，确保训练出的 PCA/PatchCore 资产与在线 `NCHW` 输入通道一致；只有显式传 `--channel-order` 时才覆盖通道顺序。独立调试入口可写 JSONL 明细；PatchCore 训练主链路直接写 `.npy` embedding 矩阵。
- `training_tools.evaluate_pipeline` 调用在线 `InferenceEngine`，按 manifest 中的人工标注或弱标签计算整体、ROI、camera 和 split 指标；匹配只看 bbox/score，不看缺陷类别。
- `training_tools.simulate_capture_detection` 从 `images_capture/` 平铺 PNG 选择同一序号的两机位三光源样本，构造 `SeatInspectionJob`，调用生产配方完整检测链路；默认只写最终检测报告 `detection_summary.json`、原图 `original_images/` 和可查看检测图 `detection_images/`，OK/NG/RECHECK/ERROR 都会生成检测图。只有显式传 `--write-trace` 时才额外输出完整 trace；它只做 Python-only 离线算法模拟，不控制 PLC、相机或频闪，也不经过共享内存。需要验证 C++ 写 SHM + Python 读 SHM 时，使用 `uv run python tools/run_simulated_ipc.py --replay-capture`。
- `training_tools.collect_trace_dataset` 同时兼容旧 trace 目录 `images/<camera>/<roi>/<light>.png` 和新目录 `images/<camera>/<pose>/<roi>/<light>.png`，生成的样本路径与 manifest 都包含 `pose_id`，避免机器人飞拍同一末端相机下的不同 pose 互相覆盖。
- `training_tools.collect_shm_dataset` 调用在线 `ShmClient`、`SeatSurfaceAoiAlgorithm` 和 `TraceWriter`，从 C++ 共享内存任务获取多相机多光源图像，保存按 `camera_id/pose_id` 分目录的 `raw_images/`、`raw_frame_manifest.jsonl`，并生成 trace/训练 manifest；它不控制 PLC、相机或频闪。
- `training_tools.collect_capture_dataset` 用于现场 `capture_only` 平铺 PNG，例如 `TOP_BACK_<timestamp>_L1_original.png`。它按相机和光源序号组包，默认按当前配方 `light_order` 生成 `L1/L2/L3...` 映射；当前生产配方即 `L1/L2/L3 -> DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT`。工具会调用当前配方的 `onnx_yolo_seg` ROI 模型先按 mask 外接矩形裁出真实座椅 ROI，再把 mask 外像素置黑，只保留 mask 内目标物体，最后生成 ROI PNG 和 manifest。默认保留 ROI 原生尺寸，避免把纹理和细小缺陷压缩失真；只有在需要与 PatchCore 固定输入尺寸对齐时，才应显式传 `--roi-output-size WIDTHxHEIGHT`，并采用等比例 letterbox 缩放。`dataset_summary.json` 记录 `roi_size_policy` 和 ROI 尺寸分布，`patchcore_training_summary.json` 记录实际训练输入 `input_shape_summary`。ROI 多候选冲突、低置信、越界或缺光源样本会跳过或失败，不进入训练集。
- `training_tools.train_patchcore_assets` 训练 PatchCore safety net 所需的 embedding/PCA/memory bank/FAISS 资产；`training_tools.export_wideresnet_embedding` 生成生产配方引用的 WideResNet50 embedding ONNX。
- `training_tools.train_patchcore_assets` 使用真实 OK 样本训练 PatchCore 无监督主模型所需的 embedding/PCA/memory bank/FAISS 资产，并把 `input_channels`、`spatial_upsample_height/width`、`spatial_layers`、manifest hash 和 embedding ONNX hash 写入 bank metadata；`tools.validate_model_assets` 会校验这些契约，避免配方和旧 bank 静默错配。PatchCore bank 采用 `seat_patchcore_bank.json` 元数据 + `seat_patchcore_bank.npy` float32 向量矩阵，不再支持 JSON 内嵌向量；训练默认清理 `embeddings.npy/pca_embeddings.npy` 中间矩阵，排障时可传 `--keep-intermediate-embeddings` 保留。`training_tools.build_faiss_index` 写出索引后会校验维度和向量数，`FlatL2` 额外做 smoke search，`IVFFlat` 的召回和延迟由部署评估阶段验证；FAISS 测试用子进程隔离，避免 Windows 上不同 OpenMP runtime 在同一 pytest 进程内互相污染；`training_tools.export_wideresnet_embedding` 生成生产配方引用的 WideResNet50 embedding ONNX。

### 融合、规则和追溯

`FusionEngine` 对同一 camera/pose/ROI 执行 IoU NMS，合并 evidence lights，并限制每个 ROI 候选数。NMS 抑制数和候选容量溢出数分开统计；如果容量溢出隐藏了候选且规则原本会输出 `OK`，`RuleEngine` 会改为 `RECHECK` 并写入 `CONFIGURATION_ERROR`，避免用 `OK` 掩盖算法不确定性。`DefectFilter` 和 `RuleEngine` 根据单一 `decision_threshold`、面积阈值、长宽比阈值和候选分数输出 `OK`、`NG`、`RECHECK` 或 `ERROR`。长宽比过滤（`min_aspect_ratio` / `max_aspect_ratio`）用于排除长条形噪声，不通过的 NG 候选降级为 RECHECK。缺陷结果不携带缺陷类别字段，只表达位置、分数、证据光源和处置判定。

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
- 原始采集 PNG 图、ROI PNG 图和检测 overlay PNG 图；原始图路径为 `raw_images/<camera_id>/<pose_id>/<light_id>.png`，ROI 图路径为 `images/<camera_id>/<pose_id>/<roi_name>/<light_id>.png`，overlay 路径为 `overlays/<camera_id>/<pose_id>/<roi_name>.png`。overlay 以匹配光源的 raw 原图为底图，尺寸等于 raw 原图；anomaly_map 从 ROI 坐标映射回 raw 坐标后只在最终缺陷候选 bbox 内叠加达到二值化阈值的热区，低分区域不再整片染蓝或染黄，缺陷 bbox 按 raw 坐标绘制。PNG filter scanline 构造、raw 灰度转 RGB、heatmap 上采样/坐标映射/alpha blend 和矩形绘制均使用 `numpy` 批量处理，仍使用内置 zlib PNG writer，不新增图像编码依赖。只要该 ROI 已完成预处理，OK、NG、RECHECK 和 ERROR trace 都会写检测 overlay；NG/RECHECK/ERROR 有缺陷候选时额外绘制候选 bbox。

在线 `SeatSurfaceAoiAlgorithm` 调用 `TraceWriter` 时，如果任一 JSON、原始图、ROI 图或 overlay 写入失败，会记录 `context["trace_error"]`，并把当前结果改为 `RECHECK`、`error_code=DEVICE_FAULT`、`quality_pass=false` 后再交给 `ShmClient` 写回 C++。展示通道 `display_latest.json` / `display_events.jsonl` 属于结果发布后的只读前端辅助输出，失败只打印告警，不反向修改已经发布的共享内存结果。

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

```powershell
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
uv run python -m tools.validate_architecture_readiness --scope reference
uv run python -m tools.validate_deployment_preflight
uv run pytest python_detector/tests/test_run_simulated_ipc_tool.py
uv run python -m training_tools.replay_dataset --count 3 --write-trace
uv run python tools/run_simulated_ipc.py
uv run python tools/run_simulated_ipc.py --config cpp_controller/config/station_runtime.test.conf
uv run python tools/run_simulated_ipc.py --replay-capture
```

有 trace 数据后再执行离线样本生成：

```powershell
uv run python -m training_tools.collect_trace_dataset --trace-root trace --output datasets/seat_trace_v1
uv run python -m training_tools.collect_shm_dataset --output datasets/seat_shm_v1 --max-jobs 10 --trace-root trace/training_shm
uv run python -m training_tools.collect_capture_dataset --input images_capture/20260623/LINE1_AOI_CAPTURE_MANUAL_SEAT_9000 --output datasets/seat_capture_20260623_9000 --recipe seat_a_black_leather_production_v1 --split train --label-status unverified_ok --skip-failed
uv run python -m training_tools.simulate_capture_detection --input images_capture/20260623/LINE1_AOI_CAPTURE_MANUAL_SEAT_9000 --output reports/capture_detection_20260623_9000 --recipe seat_a_black_leather_production_v1 --sample-index 1
# 仅在需要与固定 PatchCore 输入尺寸对齐时再传 --roi-output-size，例如 1536x2048；缩放方式为等比例 letterbox。
# --input-channels 必须显式等于所选配方 models.<key>.input_channels 数量；当前生产配方为 3。
uv run python -m training_tools.export_wideresnet_embedding --output model/wideresnet50/seat_wrn50_embedding.onnx --input-channels 3 --spatial-mode --spatial-layers layer2,layer3
# 可选调试：输出逐 patch JSONL 明细；正式 PatchCore 训练不依赖该文件。
uv run python -m training_tools.extract_embeddings --manifest datasets/seat_trace_v1/dataset_manifest.jsonl --output datasets/seat_trace_v1/embeddings.jsonl --backend statistical
uv run python -m training_tools.train_patchcore_assets --manifest datasets/seat_roi_train/dataset_manifest.jsonl --output-dir model/patchcore --recipe seat_a_black_leather_production_v1 --model-key patchcore_detector --embedding-backend onnx_wideresnet50 --embedding-model model/wideresnet50/seat_wrn50_embedding.onnx --spatial-mode --spatial-layers layer2,layer3 --spatial-upsample-height 256 --spatial-upsample-width 256 --pca-components 524 --pca-version pca_seat_v3 --bank-version bank_v4_spatial256 --coreset-ratio 0.1 --coreset-method stride --build-faiss
uv run python -m training_tools.evaluate_pipeline --manifest datasets/seat_trace_v1/dataset_manifest.jsonl --output reports/evaluation_report.json --split test
uv run python -m training_tools.train_roi_yolo --data datasets/roi_seg/dataset.yaml --task segment --imgsz 1024 --output model/roi_yolo/seat_roi_seg.onnx
```

涉及 ONNX 后端时：

```powershell
uv sync --group dev --extra onnx
uv run pytest python_detector/tests/test_model_backend.py
```

涉及 FAISS 加速时：

```powershell
uv sync --group dev --extra onnx --extra faiss
uv run pytest python_detector/tests/test_v4_alignment_modules.py
```
