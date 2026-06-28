# Python 检测算法与模型运维

本文整合原 Python 模块规范、配方设计、标定与 ROI、模型后端、追溯回放和部署说明。`python_detector` 是独立检测进程与算法模块，只负责从 `SeatInspectionJob` 到 `InspectionResult` 的检测链路；PLC、相机、频闪和触发时序由 C++ 主控负责。

## 模块边界

```text
SeatInspectionJob
  -> ImageQualityGate
  -> Preprocessor
  -> RoiLocator
  -> ReflectanceCubeBuilder / EccRegistration
  -> FeatureBuilder
  -> InferenceEngine
  -> FusionEngine / DefectFilter
  -> RuleEngine
  -> InspectionResult
```

公开入口：

- `python_detector.SeatSurfaceAoiAlgorithm`：纯算法入口，适合回放、测试、离线验证和嵌入式调用。
- `python_detector.InspectionPipeline`：流水线编排入口，适合单元测试和替换子模块。
- `python_detector.detector_main`：在线检测进程入口，只负责共享内存循环和结果发布。
- `seat-aoi-detector`：安装包命令行入口，等价于 `uv run python -m python_detector.detector_main`。

依赖方向应保持单向：

```text
config / ipc data types
  -> pipeline pure logic
  -> models runtime adapters
  -> algorithm facade
  -> detector_main IPC process
```

纯图像、特征和规则函数不要读写共享内存、文件系统或全局配置。模型后端必须通过统一接口返回 `DefectCandidate`，后端异常必须包装为保守错误。

## 环境与依赖

根目录 `pyproject.toml` 和 `uv.lock` 是 Python 算法层的依赖入口：

- 默认依赖：`PyYAML`，用于配方、标定和 ROI YAML。
- `test` dependency group：`pytest`、`numpy`。
- `dev` dependency group：`pytest`、`numpy`、`ruff`。
- `onnx` extra：`numpy`、`onnxruntime`，用于 YOLO ROI、WideResNet50 和可选 ONNX detection 实验后端。
- `faiss` extra：用于 PatchCore FAISS 索引加速。

默认 fake/statistical/PatchCore exact KNN 参考链路不依赖 ONNX Runtime 或 FAISS。缺少可选后端依赖、模型文件或输出解码配置时，必须返回 `RECHECK` 或 `ERROR`，不能输出 `OK`。

```powershell
uv sync --group dev
uv sync --group dev --extra onnx
uv sync --group dev --extra onnx --extra faiss
uv sync --locked --no-dev
```

上 Windows 工控机前使用部署预检区分本地可实现项和现场项：

```powershell
uv run python -m tools.validate_deployment_preflight
uv run python -m tools.validate_deployment_preflight --strict-production
```

默认模式确认参考链路、Windows 共享内存映射、跨平台 IPC、部署包入口和 PLC 前手动联调路径无本地阻塞；严格模式把固定双机位正式生产配置缺失、生产光源/配方不一致和真实模型资产缺失作为阻塞。

## IPC 与协议

`python_detector/ipc/shm_protocol.py` 必须与 `cpp_controller/include/ipc/shm_protocol.hpp` 保持二进制布局一致。当前协议为 v2，每帧携带 `camera_id`、`pose_id`、`shot_id`、机器人时间戳和 TCP 位姿；Python 按 `(camera_id, pose_id)` 组包为 `CameraBundle`。

修改协议时必须同步：

- C++ 协议结构体和 ring buffer。
- Python `shm_protocol.py`、`shm_client.py`、`data_types.py`。
- `tools.validate_protocol` 和相关测试。
- [共享内存协议](shm_protocol.md)、根目录 README 和 `python_detector/README.md`。

`ShmClient` 会校验 header CRC、payload CRC、slot 状态、序列号、payload 边界、重复 camera/pose/light 等安全条件。解析失败会发布保守错误结果并释放输入 slot。

## 配方

默认配方位于：

```text
python_detector/config/default_recipe.yaml
```

必须包含：

- `recipe_id`
- `sku`
- `light_order`
- `v4_lights`
- `camera_defaults`
- `cameras`
- `quality`
- `roi_locator`
- `registration`
- `decision_threshold`
- `models`
- `trace`

`camera_defaults` 用来放同一配方内各视角共享的模型、ROI 模板、基准光源、光源顺序和 ROI 级模型映射，避免每个机位重复写同一组字段。`cameras` 在当前 schema 中表示检测视角配置，不只表示物理相机；相机条目会继承 `camera_defaults`，只需要写差异字段，例如固定机位的 `calibration_id`，或机器人飞拍的 `camera_id`、`pose_id` 和 `calibration_id`。固定机位方案通常让 `pose_id == camera_id`；机器人飞拍方案允许多个视角共享同一个末端相机 `camera_id=EYE_IN_HAND`，并通过不同 `pose_id` 选择 ROI、标定和模型配置。

旧配方继续支持在每个 `cameras.<view>` 下显式写 `model_key`、`safety_net_model_key`、`roi_template`、`base_light_id`、`light_order`、`roi_models` 和 `roi_safety_net_models`；这些字段会覆盖 `camera_defaults`。图像尺寸、像素尺寸和多光源对齐矩阵属于标定事实，优先维护在 `calibration/*.yaml` 中，不再在内置配方里重复写 `pixel_size_mm`。

机器人飞拍示例：

```text
python_detector/config/robot_flyshot_recipe.yaml
cpp_controller/config/station_runtime.robot_flyshot.example.conf
```

两侧必须同时对齐 `recipe_id`、`camera_id`、`pose_id`、`calibration_id` 和 `light_order`。

### V4 光源映射

当前固定双机位产线使用 3 个必需检测语义光源。`DOME` ROI 定位语义当前暂映射到第一路 `DIFFUSE`，不额外要求 C++ 发布 `DOME_ROI` 采集轮次；质量门禁和模型输入要求三路检测光：

```yaml
quality:
  required_lights:
    - DIFFUSE
    - POLAR_DIFFUSE
    - HIGH_LEFT
```

V4.0 语义光源通过 `v4_lights.semantic_to_light_id` 映射到当前协议 light id：

```yaml
v4_lights:
  semantic_to_light_id:
    DOME: DIFFUSE
    DARKFIELD_L: HIGH_LEFT
    BRIGHTFIELD: POLAR_DIFFUSE
```

`DOME` 当前映射到 `DIFFUSE`，只用于为 ROI 定位后端选择输入图；`DARKFIELD_L` 和 `BRIGHTFIELD` 分别映射到 `HIGH_LEFT` 与 `POLAR_DIFFUSE`，属于当前检测链路必需语义。`DARKFIELD_R/HIGH_RIGHT` 和独立 `DOME_ROI` 是预留扩展光源，不属于当前 3 个检测光源生产配方。增强光源如 `LOW_LEFT`、`LOW_RIGHT`、`HIGH_FRONT`、`HIGH_REAR`、`NIR` 只能作为 ROI 增强光源，不能成为默认输出 `OK` 的隐藏依赖。

当前固定机位工控机配置 `cpp_controller/config/station_runtime.production.conf` 采集 `1,2,3` 三个轮次：`1 -> DIFFUSE`、`2 -> POLAR_DIFFUSE`、`3 -> HIGH_LEFT`。`production_recipe.yaml` 顶层 `light_order` 与 C++ 采集顺序一致，`camera_defaults.light_order`、`quality.required_lights` 和模型输入通道均为这三路检测光，模型输入通道为 `ch0_diffuse/ch1_polar_diffuse/ch2_high_left`。如果未来增加常亮 `DOME_ROI` 或第 4 路 `HIGH_RIGHT`，需要同步 C++ 配置、生产配方、模型输入通道、训练资产和相关测试。

### ROI 定位与配准

ROI 定位使用 `roi_locator`：

```yaml
roi_locator:
  backend: template
  dome_semantic_light: DOME
  model_path: models/roi.onnx
  min_confidence: 0.50
  max_pose_error_px: 4.0
  input_width: 1024
  input_height: 1024
  input_channels: 3
  output_decode: yolo_xyxy_rows
  bbox_format: xyxy_pixel
  class_names: [seat]
  fail_policy: RECHECK
```

支持后端：

- `template`：使用 ROI 模板，适合模拟、夹具稳定样件或兜底验证。
- `fake_yolo`：按模板生成可追溯 YOLO 行，用于测试 YOLO 输出到 ROI 模板坐标系的转换链路。
- `onnx_yolo`：读取 Dome ROI 图，调用 ONNX 模型，解析 `[x1, y1, x2, y2, score, class_id]`。
- `onnx_yolo_seg`：读取 Dome ROI 图，调用 YOLO segmentation ONNX，按 mask 自动生成运行时 `polygon_xy`；模板只作为安全边界。后续预处理会按 mask 外接框裁出 ROI，并将 mask 外像素置黑，使模型输入只保留 mask 内目标物体。

缺 Dome ROI 图、输出越界、置信度不足、seg mask 面积异常、越出安全边界、姿态误差超差或 ROI 名称未映射时返回 `RECHECK`。
配置了 `input_width/input_height` 时，ROI 定位输入会先 letterbox 到模型训练尺寸；`input_channels=3` 会把当前 `DOME` 语义映射到的 Mono8 图复制为 3 通道，匹配 Ultralytics segmentation ONNX 的常见输入。

配准使用 `registration.method`：

```yaml
registration:
  base_light_id: POLAR_DIFFUSE
  base_light_fallback: DIFFUSE
  fail_policy: RECHECK
  method: ecc
  max_iterations: 30
  convergence_epsilon: 0.0001
  search_radius_px: 2
  min_correlation: 0.05
```

`fixed_calibration` 使用标定文件 `light_alignment.matrix_3x3` 做误差检查。`ecc` 使用在线 ROI 平移搜索参考实现，输出矩阵、平移量、相关系数、迭代次数、收敛状态和误差报告；失败时返回 `RECHECK`。

## 标定与 ROI 文件

默认标定：

```text
python_detector/config/calibration/<camera_id>/simulated_v1.yaml
```

默认 ROI：

```text
python_detector/config/roi/default_roi.yaml
```

校验要求：

- 图像 `calibration_id` 必须和配方/标定文件一致。
- 图像尺寸必须和标定文件一致。
- 配方声明的 `roi_template` 文件必须存在，不允许静默回退。
- ROI 至少包含 3 个点；非轴对齐 ROI 必须提供 4 个点并配置 `output_size`。
- `light_alignment.<light>.matrix_3x3` 必须包含 9 个有限数字。
- 当前在线主链路只支持 `MONO8` ROI 输入。
- ROI 定位失败、局部对齐误差超限、ROI 模板缺失或坐标越界时不能输出 `OK`。

Python 检测进程会缓存标定解析结果，缓存 key 包含 `camera_id`、`calibration_id` 和 ROI 模板路径，避免多 SKU 或多 ROI 版本复用错误 ROI。

测试机更新标定流程：

1. 按机位或 pose 生成独立标定文件。
2. 更新配方中的 `cameras.<view>.calibration_id`；如果 ROI 安全边界文件发生变化，再更新 `camera_defaults.roi_template` 或单个视角的 `roi_template` 覆盖。
3. 用标准样件验证 ROI 定位、局部对齐、ROI 边界、清晰度、曝光和多光源对齐。
4. 标定变更必须同步更新相关文档并形成 commit。

## 质量门禁

`ImageQualityGate` 在进入模型前拦截不可靠输入，包括缺少必需机位/光源、非单调时间戳、重复帧号、曝光/增益漂移、过曝欠曝、锐度不足、运动模糊、光源亮度漂移和配准误差。

常用字段：

```yaml
quality:
  max_saturation_ratio: 0.40
  max_dark_ratio: 0.40
  min_mean_gray: 0
  max_mean_gray: 255
  min_sharpness: 1.0
  min_motion_gradient: 1.0
  max_light_mean_delta: 80
  max_capture_span_us: 500000
  max_exposure_delta_us: 200
  max_gain_delta: 0.2
  require_monotonic_timestamps: true
  require_unique_frame_indices: true
```

生产配方当前将单帧过曝像素比例和过暗像素比例阈值设为 `0.40`；缺帧、协议错误、时序异常、曝光/增益一致性异常、锐度不足、运动梯度不足和配准失败仍进入 `RuleEngine.make_quality_fail_result()`，不能输出 `OK`。

## 模型后端

当前支持：

- `fake`：测试用固定候选。
- `onnx_detection_rows`：通用 ONNX 检测行输出。
- `ultralytics_yolo`：Ultralytics 检测 ONNX 输出，在线解码为 `[x1, y1, x2, y2, score, class_id]` 行表。
- `onnx_yolo_seg`：Dome ROI segmentation 定位，推荐生产使用。
- `onnx_yolo`：Dome ROI bbox 定位，保留兼容。
- `statistical_embedding`：参考 embedding 后端。
- `onnx_wideresnet50`：WideResNet50 embedding 入口。
- `patchcore_knn`：PatchCore 无监督异常检测主模型或可选 safety net，优先 FAISS，缺索引或缺依赖时回退 exact KNN。

模型角色：

- `primary`：主检测模型；当前生产配方使用 PatchCore 无监督异常检测主模型。
- `safety_net`：可选安全网模型，用于叠加其它未知或罕见异常检测策略。

PatchCore 可以作为当前生产无监督主检测模型，也可以在实验配方中作为 `safety_net`。memory bank、PCA 或 embedding 维度不一致时返回 `ERROR`。

真实模型资产默认放在根目录 `model/`：

```text
model/roi_yolo/seat_roi_seg.onnx
model/wideresnet50/seat_wrn50_embedding.onnx
model/patchcore/seat_pca.json
model/patchcore/seat_patchcore_bank.json
model/patchcore/seat_patchcore_bank.npy
model/patchcore/seat_patchcore.faiss
```

`seat_patchcore_bank.json` 只保存版本、维度、`vector_count`、`vectors_path` 和训练 metadata；PatchCore 向量矩阵必须放在 `seat_patchcore_bank.npy`，不再支持把大体积向量数组内嵌到 JSON。`training_tools.train_patchcore_assets` 默认训练结束后清理 `embeddings.npy/pca_embeddings.npy` 中间矩阵，只有排障时才通过 `--keep-intermediate-embeddings` 保留。

上线前校验：

```powershell
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
```

仓库默认占位文件未替换时这些命令应失败，并列出需要替换的真实模型产物。`production_model.example.yaml` 仍保留为参考模板；在线生产链路应使用 `seat_a_black_leather_production_v1` 或 `seat_a_robot_flyshot_production_v1`。

## Trace、回放与训练闭环

默认追溯根目录为 `trace/`。一次 trace 可能包含：

- `job.json`
- `result.json`
- `recipe_summary.json`
- `quality_report.json`
- `roi_location_report.json`
- `registration_report.json`
- `feature_summary.json`
- `fusion_summary.json`
- `timings.json`
- `error.json`
- `raw_images/<camera_id>/<pose_id>/<light_id>.png`
- `images/<camera_id>/<pose_id>/<roi_name>/<light_id>.png`
- `overlays/<camera_id>/<pose_id>/<roi_name>.png`

`overlays/` 是唯一检测可视化 PNG：它使用 PatchCore anomaly_map，但只在最终缺陷候选 bbox 内叠加阈值以上热区并绘制判定框，避免额外生成调试热力图污染 trace。

保存策略：

- `RECHECK`、`ERROR`、`NG` 默认保存。
- `OK` 默认不保存，可通过 `trace.save_ok_ratio` 做确定性抽样。

回放：

```powershell
uv run python -m training_tools.replay_dataset --count 3 --write-trace
```

Trace 转训练样本：

```powershell
uv run python -m training_tools.collect_trace_dataset `
  --trace-root trace `
  --output datasets/seat_trace_v1
```

输出包括 `dataset_manifest.jsonl`、`dataset_summary.json` 和 ROI 图像副本。manifest 中已有 defect 只作为弱标签来源，不代表人工标注结论。

从 C++ 共享内存任务直接生成离线样本：

```powershell
uv run python -m training_tools.collect_shm_dataset `
  --output datasets/seat_shm_v1 `
  --max-jobs 10 `
  --trace-root trace/training_shm
```

该入口只复用 Python detector 的 `ShmClient`、算法流水线和 trace 写入能力，消费 C++ 已采集并写入共享内存的多相机多光源图像；它会保存 `raw_images/`、`raw_frame_manifest.jsonl` 和可训练 ROI manifest，不控制 PLC、相机、机器人或频闪。

从 `capture_only` 平铺 PNG 采图目录生成 ROI manifest：

```powershell
uv run python -m training_tools.collect_capture_dataset `
  --input images_capture\20260623\LINE1_AOI_CAPTURE_MANUAL_SEAT_9000 `
  --output datasets\seat_capture_20260623_9000 `
  --recipe seat_a_black_leather_production_v1 `
  --split train `
  --label-status unverified_ok `
  --skip-failed
```

该入口按文件名中的 `TOP_BACK/TOP_CUSHION`、`L1/L2/L3` 和时间戳组包，默认将三路采集光映射为 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT`，调用当前配方的 `seat_roi_seg.onnx` 做 ROI 定位并输出三光源 ROI PNG。默认保留 segmentation 裁出的原生 ROI 尺寸，并将 mask 外像素置黑，只保留 mask 内目标物体；只有需要与固定 PatchCore 输入尺寸对齐时，才显式传 `--roi-output-size WIDTHxHEIGHT`，缩放方式为等比例 letterbox。`dataset_summary.json` 记录 `roi_size_policy` 和 ROI 尺寸分布，`patchcore_training_summary.json` 记录实际训练输入 `input_shape_summary`。ROI 多候选冲突、低置信、越界或缺光源样本会跳过或失败，不进入 PatchCore 正常库。`unverified_ok` 只表示采集来源未被人工标注，训练正式阈值前必须人工确认正常/缺陷标签。

从 `images_capture` 抽取同一序号的两机位三光源样本做完整链路模拟，并生成可查看检测图：

```powershell
uv run python -m training_tools.simulate_capture_detection `
  --input images_capture\20260623\LINE1_AOI_CAPTURE_MANUAL_SEAT_9000 `
  --output reports\capture_detection_20260623_9000 `
  --recipe seat_a_black_leather_production_v1 `
  --sample-index 1
```

该入口默认只写出最终检测报告 `detection_summary.json`、原图 `original_images/` 和检测图 `detection_images/`，用于检查当前 ROI 定位、PatchCore 判定和检测图是否一致；OK、NG、RECHECK 和 ERROR 都会生成检测图。需要完整 JSON、ROI 图、raw 图和 overlay trace 排障时，再显式追加 `--write-trace`。
导出 PatchCore 所需 WideResNet50 embedding ONNX：

```powershell
uv run python -m training_tools.export_wideresnet_embedding `
  --output model/wideresnet50/seat_wrn50_embedding.onnx `
  --input-channels 3 `
  --embedding-dim 1024
```

从真实 ROI 多光源样本提取 embedding：

```powershell
uv run python -m training_tools.extract_embeddings `
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl `
  --output datasets/seat_trace_v1/embeddings.jsonl `
  --backend statistical
```

该入口用于离线排障和审计，可输出逐样本 JSONL 明细；正式 PatchCore 资产训练不依赖这个 JSONL 文件。

训练 PatchCore PCA、memory bank 和可选 FAISS：

```powershell
uv run python -m training_tools.train_patchcore_assets `
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl `
  --output-dir model/patchcore `
  --split train `
  --pca-components 3 `
  --coreset-ratio 0.1 `
  --build-faiss
```

评估当前配方模型：

```powershell
uv run python -m training_tools.evaluate_pipeline `
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl `
  --output reports/evaluation_report.json `
  --split test
```

Benchmark：

```powershell
uv run python -m training_tools.benchmark_pipeline --count 10
uv run python -m training_tools.benchmark_pipeline `
  --count 20 `
  --max-avg-ms 80 `
  --max-ms 120 `
  --max-step-ms quality_ms=10 `
  --max-step-ms inference_ms=30
```

## 新增 SKU 流程

1. 复制默认配方。
2. 修改 `sku`、检测视角、光源顺序和 V4 语义映射。
3. 修改质量阈值、缺陷阈值和模型引用。
4. 指向对应标定文件和 ROI 模板。
5. 固定机位或机器人飞拍配置同步对齐 `recipe_id`、`camera_id`、`pose_id`、`calibration_id` 和 `light_order`。
6. 运行 schema、协议、回放和模拟 IPC 验证。

模型资产补齐后的生产配方入口：

- 固定机位：`python_detector/config/production_recipe.yaml`，`recipe_id=seat_a_black_leather_production_v1`。
- 机器人飞拍：`python_detector/config/production_robot_flyshot_recipe.yaml`，`recipe_id=seat_a_robot_flyshot_production_v1`。
- 两者都启用 `onnx_yolo_seg` ROI segmentation、`ecc` 配准、WideResNet50 embedding、PCA、PatchCore KNN 和可选 FAISS 无监督异常检测主模型。
- 仓库内 `production_full_roi.yaml` 和 `*production*.yaml` 标定文件只是可校验模板；ROI 模板中的 `polygon_xy` 用作安全边界和 `output_size` 约束，现场必须用真实像素尺寸、多光源对齐矩阵和 segmentation 训练产物替换。

## 验证命令

```powershell
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
uv run python -m tools.validate_architecture_readiness --scope reference
uv run python tools/run_simulated_ipc.py
```

生产阈值必须基于人工确认标注，并按 ROI、材质、颜色、机位、光源条件和缺陷尺寸分层验证召回与误报。弱标签 trace 只能用于闭环排查和预训练资产准备。
