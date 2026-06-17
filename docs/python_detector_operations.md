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
- `onnx` extra：`numpy`、`onnxruntime`，用于 ONNX/YOLO/WideResNet50。
- `faiss` extra：用于 PatchCore FAISS 索引加速。

默认 fake/statistical/PatchCore exact KNN 参考链路不依赖 ONNX Runtime 或 FAISS。缺少可选后端依赖、模型文件或输出解码配置时，必须返回 `RECHECK` 或 `ERROR`，不能输出 `OK`。

```bash
uv sync --group dev
uv sync --group dev --extra onnx
uv sync --group dev --extra onnx --extra faiss
uv sync --locked --no-dev
```

上 Windows 工控机前使用部署预检区分本地可实现项和现场项：

```bash
uv run python -m tools.validate_deployment_preflight
uv run python -m tools.validate_deployment_preflight --strict-production
```

默认模式确认参考链路、Windows 共享内存映射、跨平台 IPC、部署包入口和 PLC 前手动联调路径无本地阻塞；严格模式把正式生产配置和真实模型资产缺失作为阻塞。

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
- `cameras`
- `quality`
- `roi_locator`
- `registration`
- `thresholds`
- `models`
- `trace`

`cameras` 在当前 schema 中表示检测视角配置，不只表示物理相机。固定机位方案通常让 `pose_id == camera_id`；机器人飞拍方案允许多个视角共享同一个末端相机 `camera_id=EYE_IN_HAND`，并通过不同 `pose_id` 选择 ROI、标定、模型和阈值。

机器人飞拍示例：

```text
python_detector/config/robot_flyshot_recipe.yaml
cpp_controller/config/station_runtime.robot_flyshot.example.conf
```

两侧必须同时对齐 `recipe_id`、`camera_id`、`pose_id`、`calibration_id` 和 `light_order`。

### V4 光源映射

默认生产主链路使用 4 个必需光源：

```yaml
quality:
  required_lights:
    - DIFFUSE
    - POLAR_DIFFUSE
    - HIGH_LEFT
    - HIGH_RIGHT
```

V4.0 语义光源通过 `v4_lights.semantic_to_light_id` 映射到当前协议 light id：

```yaml
v4_lights:
  semantic_to_light_id:
    DOME: DIFFUSE
    DARKFIELD_L: HIGH_LEFT
    DARKFIELD_R: HIGH_RIGHT
    BRIGHTFIELD: POLAR_DIFFUSE
```

`DOME`、`DARKFIELD_L` 和 `DARKFIELD_R` 是必需语义；`BRIGHTFIELD` 可按现场方案映射或移除。增强光源如 `LOW_LEFT`、`LOW_RIGHT`、`HIGH_FRONT`、`HIGH_REAR`、`NIR` 只能作为 ROI 增强光源，不能成为默认输出 `OK` 的隐藏依赖。

### ROI 定位与配准

ROI 定位使用 `roi_locator`：

```yaml
roi_locator:
  backend: template
  dome_semantic_light: DOME
  model_path: models/roi.onnx
  min_confidence: 0.50
  max_pose_error_px: 4.0
  output_decode: yolo_xyxy_rows
  bbox_format: xyxy_pixel
  class_names: [full]
  fail_policy: RECHECK
```

支持后端：

- `template`：使用 ROI 模板，适合模拟、夹具稳定样件或兜底验证。
- `fake_yolo`：按模板生成可追溯 YOLO 行，用于测试 YOLO 输出到 ROI 模板坐标系的转换链路。
- `onnx_yolo`：读取 Dome ROI 图，调用 ONNX 模型，解析 `[x1, y1, x2, y2, score, class_id]`。

缺 Dome 图、输出越界、置信度不足、姿态误差超差或类别未映射时返回 `RECHECK`。

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
2. 更新配方中的 `calibration_id` 和 `roi_template`。
3. 用标准样件验证 ROI 定位、局部对齐、ROI 边界、清晰度、曝光和多光源对齐。
4. 标定变更必须同步更新相关文档并形成 commit。

## 质量门禁

`ImageQualityGate` 在进入模型前拦截不可靠输入，包括缺少必需机位/光源、非单调时间戳、重复帧号、曝光/增益漂移、过曝欠曝、锐度不足、运动模糊、光源亮度漂移和配准误差。

常用字段：

```yaml
quality:
  max_saturation_ratio: 0.01
  min_mean_gray: 20
  max_mean_gray: 235
  min_sharpness: 1.0
  min_motion_gradient: 1.0
  max_light_mean_delta: 80
  max_capture_span_us: 500000
  max_exposure_delta_us: 200
  max_gain_delta: 0.2
  require_monotonic_timestamps: true
  require_unique_frame_indices: true
```

质量失败进入 `RuleEngine.make_quality_fail_result()`，不能输出 `OK`。

## 模型后端

当前支持：

- `fake`：测试用固定候选。
- `onnx_detection_rows`：通用 ONNX 检测行输出。
- `ultralytics_yolo`：Ultralytics 检测 ONNX 输出，在线解码为 `[x1, y1, x2, y2, score, class_id]` 行表。
- `onnx_yolo`：Dome ROI 定位。
- `statistical_embedding`：参考 embedding 后端。
- `onnx_wideresnet50`：WideResNet50 embedding 入口。
- `patchcore_knn`：PatchCore safety net，优先 FAISS，缺索引或缺依赖时回退 exact KNN。

模型角色：

- `primary`：主检测模型，用于已知缺陷。
- `safety_net`：安全网模型，用于未知或罕见异常。

PatchCore 只能作为 `safety_net`，不能作为主检测模型。memory bank、PCA 或 embedding 维度不一致时返回 `ERROR`。

真实模型资产默认放在根目录 `model/`：

```text
model/roi_yolo/seat_roi_yolo.onnx
model/supervised_defect/seat_defect_detector.onnx
model/wideresnet50/seat_wrn50_embedding.onnx
model/patchcore/seat_pca.json
model/patchcore/seat_patchcore_bank.json
model/patchcore/seat_patchcore.faiss
```

上线前校验：

```bash
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
- `images/<camera_id>/<roi_name>/<light_id>.pgm`
- `overlays/*.ppm`

保存策略：

- `RECHECK`、`ERROR`、`NG` 默认保存。
- `OK` 默认不保存，可通过 `trace.save_ok_ratio` 做确定性抽样。

回放：

```bash
uv run python -m training_tools.replay_dataset --count 3 --write-trace
```

Trace 转训练样本：

```bash
uv run python -m training_tools.collect_trace_dataset \
  --trace-root trace \
  --output datasets/seat_trace_v1
```

输出包括 `dataset_manifest.jsonl`、`dataset_summary.json` 和 ROI 图像副本。manifest 中已有 defect 只作为弱标签来源，不代表人工标注结论。

从 C++ 共享内存任务直接生成离线样本：

```bash
uv run python -m training_tools.collect_shm_dataset \
  --output datasets/seat_shm_v1 \
  --max-jobs 10 \
  --trace-root trace/training_shm
```

该入口只复用 Python detector 的 `ShmClient`、算法流水线和 trace 写入能力，消费 C++ 已采集并写入共享内存的多相机多光源图像；它会保存 `raw_images/`、`raw_frame_manifest.jsonl` 和可训练 ROI manifest，不控制 PLC、相机、机器人或频闪。

导出 PatchCore 所需 WideResNet50 embedding ONNX：

```bash
uv run python -m training_tools.export_wideresnet_embedding \
  --output model/wideresnet50/seat_wrn50_embedding.onnx \
  --embedding-dim 1024
```

从真实 ROI 多光源样本提取 embedding：

```bash
uv run python -m training_tools.extract_embeddings \
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl \
  --output datasets/seat_trace_v1/embeddings.jsonl \
  --backend statistical
```

训练 PatchCore PCA、memory bank 和可选 FAISS：

```bash
uv run python -m training_tools.train_patchcore_assets \
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl \
  --output-dir model/patchcore \
  --split train \
  --pca-components 3 \
  --coreset-ratio 0.1 \
  --build-faiss
```

评估当前配方模型：

```bash
uv run python -m training_tools.evaluate_pipeline \
  --manifest datasets/seat_trace_v1/dataset_manifest.jsonl \
  --output reports/evaluation_report.json \
  --split test
```

Benchmark：

```bash
uv run python -m training_tools.benchmark_pipeline --count 10
uv run python -m training_tools.benchmark_pipeline \
  --count 20 \
  --max-avg-ms 80 \
  --max-ms 120 \
  --max-step-ms quality_ms=10 \
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
- 两者都启用 `onnx_yolo` ROI、`ecc` 配准、监督 ONNX 主检测、WideResNet50 embedding、PCA、PatchCore KNN 和可选 FAISS safety net。
- 仓库内 `production_full_roi.yaml` 和 `*production*.yaml` 标定文件只是可校验模板；现场必须用真实 ROI、像素尺寸和多光源对齐矩阵替换。

## 验证命令

```bash
uv run pytest
uv run python -m tools.validate_protocol
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_model_assets --recipe seat_a_robot_flyshot_production_v1
uv run python -m tools.validate_architecture_readiness --scope reference
bash tools/run_simulated_ipc.sh
```

生产阈值必须基于人工确认标注和按缺陷类别、ROI、材质、颜色、机位、光源条件分层的数据验证。弱标签 trace 只能用于闭环排查和预训练资产准备。
