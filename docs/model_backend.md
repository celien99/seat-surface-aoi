# 模型后端说明

## 当前后端

- `fake`：默认后端，用于模拟 OK、RECHECK、NG 分支。
- `onnx`：可选后端。未安装 `onnxruntime`、模型路径为空、模型文件不存在、占位文件未替换、输入 tensor 缺失、输出解码未配置或输出解析失败时，返回保守错误。
- `patchcore_knn`：PatchCore safety net 后端。读取 memory bank JSON，使用 unified embedding 和可选 PCA 投影；配置 FAISS 索引时优先尝试 FAISS，缺索引、缺依赖或加载失败时回退 exact KNN，输出 `unknown_anomaly` anomaly score。

embedding 支持两类后端：

- `statistical`：统计特征参考后端，用于测试和无真实权重时的可验证链路。
- `onnx_wideresnet50`：WideResNet50 或等价共享特征网络 ONNX 入口，要求 `embedding_model_path`、`embedding_dim`、`embedding_version` 与模型产物一致。

## 模型输入约定

FeatureBuilder 会按 ROI 生成 `NCHW` float tensor：

```text
shape = [1, C, H, W]
C = models.<model_key>.input_channels 的通道数
H/W = ROI 裁剪后的 output_size
value = feature_value / input_scale，并裁剪到 [0, 1]
```

默认生产标准通道：

```text
ch0_diffuse
ch1_polar_diffuse
ch2_high_left
ch3_high_right
ch4_high_max_min
```

模型配置必须明确输入通道顺序。ONNX 模型的第一个输入节点会接收该 NCHW tensor。

配方加载阶段会拒绝空输入通道、重复输入通道、空类别列表、重复类别名、非法 `fake_mode` 和越界 `score_threshold`，避免模型输出类别映射或输入 tensor 顺序在上线后才暴露问题。

输入通道进入模型后，候选结果中的 `evidence_lights` 会映射回真实光源名，便于共享内存回写和 C++ 侧追溯：

| 特征通道 | 回写证据光源 |
|---|---|
| `ch0_diffuse` | `DIFFUSE` |
| `ch1_polar_diffuse` | `POLAR_DIFFUSE` |
| `ch2_high_left` | `HIGH_LEFT` |
| `ch3_high_right` | `HIGH_RIGHT` |
| `ch4_high_max_min` | `HIGH_LEFT`, `HIGH_RIGHT` |
| `aux_specular_removed` | `DIFFUSE`, `POLAR_DIFFUSE` |
| `optional_dark_low_lr_diff` / `optional_dark_low_max_min` | `LOW_LEFT`, `LOW_RIGHT` |

## 配方示例

真实模型产物默认放在根目录 `model/`，`python_detector/config/production_model.example.yaml` 提供完整模板：

```yaml
models:
  fake_default:
    backend: fake
    fake_mode: auto
    model_family: supervised
    role: primary
  scratch_onnx:
    backend: onnx
    model_path: model/supervised_defect/seat_defect_detector.onnx
    model_family: supervised
    role: primary
    input_channels:
      - ch0_diffuse
      - ch1_polar_diffuse
      - ch2_high_left
      - ch3_high_right
      - ch4_high_max_min
    input_scale: 255.0
    class_names: [scratch, dent]
    output_decode: detection_rows
    bbox_format: xyxy_pixel
    score_threshold: 0.2
  unknown_safety_net:
    backend: patchcore_knn
    model_family: patchcore
    role: safety_net
    class_names: [unknown_anomaly]
    input_channels:
      - ch0_diffuse
      - ch1_polar_diffuse
      - ch2_high_left
      - ch3_high_right
      - ch4_high_max_min
    embedding_backend: onnx_wideresnet50
    embedding_model_path: model/wideresnet50/seat_wrn50_embedding.onnx
    embedding_version: wrn50_seat_v1
    embedding_dim: 1024
    embedding_layers: [layer2, layer3]
    pca_path: model/patchcore/seat_pca.json
    pca_version: pca_seat_v1
    memory_bank_path: model/patchcore/seat_patchcore_bank.json
    faiss_index_path: model/patchcore/seat_patchcore.faiss
    knn_k: 1
    anomaly_score_scale: 1.0
    score_threshold: 0.20
```

## 模型角色

- `primary`：ROI 主检测模型，用于已知缺陷的监督检测、分割、分类或 EfficientAD 等主流程。
- `safety_net`：未知缺陷安全网，用于 PatchCore 等异常检测模型。

`patchcore` 必须配置为 `role: safety_net`。配方 schema 会拒绝把 safety net 模型挂到 `model_key` 或 `roi_models` 主模型字段。

## 接入真实模型要求

- 明确输入尺寸、通道顺序、归一化方式。
- 明确输出 decode 规则、bbox 坐标格式和 mask 格式。
- 缺模型、后端异常、输出 decode 失败不能输出 `OK`。
- 每个 ROI 应明确主模型和可选安全网模型，不允许用单一 PatchCore 覆盖全座椅主检。
- 模型缓存按 `model_key`、后端、路径、fake 模式、模型家族、角色、输入通道、输入缩放、类别列表、输出解码、bbox 格式和分数阈值隔离；同名模型在不同配方中改变任一关键配置时会创建独立后端实例。
- PatchCore 配置还会把 embedding 后端、embedding 版本、embedding 维度、PCA 路径/版本、memory bank 路径、FAISS 索引路径、KNN 参数和 anomaly score scale 纳入缓存隔离。

上线前校验模型产物：

```bash
uv run python -m tools.validate_model_assets --recipe production_model_example
```

仓库提交的 `model/` 下目标文件是空置占位。占位文件会被校验工具判定为失败，必须由真实训练产物替换后才能作为生产配方上线。

## PatchCore memory bank

离线构建命令：

```bash
uv run python -m training_tools.build_patchcore_memory_bank \
  --input embeddings.jsonl \
  --output model/patchcore/seat_patchcore_bank.json \
  --version bank_v1 \
  --coreset-ratio 0.1 \
  --pca-version pca_seat_v1 \
  --faiss-enabled
```

旧入口 `uv run python -m tools.build_patchcore_memory_bank` 保留为兼容包装；新增离线训练支撑能力统一放在 `training_tools/`。

输入 `embeddings.jsonl` 每行可以是数字数组，也可以是包含 `embedding` 字段的 JSON object。输出 JSON 包含：

- `version`
- `model_family: patchcore`
- `embedding_dim`
- `coreset_ratio`
- `pca_version`
- `faiss_enabled`
- `vectors`

在线后端在配置 `faiss_index_path` 时会尝试加载 FAISS 索引。索引文件缺失、为空、未安装 `faiss-cpu`、维度不一致或加载失败时，后端回退 exact KNN，并在 trace 的 `anomaly_summary.backend` 和 `fallback_reason` 中记录实际状态。memory bank 缺失、维度不匹配、PCA 版本不匹配或 vectors 为空都会返回保守错误。

安装 FAISS 可选依赖：

```bash
uv sync --group dev --extra onnx --extra faiss
```

## PCA 参数

PCA 参数文件为 JSON：

```json
{
  "version": "pca_seat_v1",
  "mean": [0.0, 0.0],
  "components": [[1.0, 0.0], [0.0, 1.0]]
}
```

在线投影会校验 `version`、输入维度、均值维度和 component 维度，任何不一致都不会输出 `OK`。

## ONNX detection_rows 输出

当前 ONNX 后端支持 `output_decode: detection_rows`。模型第一个输出必须能解析为二维行表：

```text
[x1, y1, x2, y2, score, class_id]
```

也支持带 batch 维的 `[1, N, 6]`。字段含义：

- `score` 必须是 `[0, 1]` 范围内的有限值；小于 `score_threshold` 的行会被忽略。
- `class_id` 必须是整数值并落在 `class_names` 范围内，否则返回保守错误。
- `bbox_format: xyxy_pixel` 表示 bbox 是 ROI 输出图内像素坐标，结果会通过 `roi_to_source_matrix` 映射回原图 `bbox_xyxy_pixel`。
- `bbox_format: xyxy_normalized` 表示 bbox 是 ROI 输出图内归一化坐标，先按 `feature_shape_hw` 还原到 `[0, width - 1] / [0, height - 1]` 像素坐标，再通过 `roi_to_source_matrix` 映射回原图 `bbox_xyxy_pixel`。
- bbox 越界、反向、包含 NaN/Inf、输出为空或形状不是 `[N, >=6]` 时返回保守错误，不会对模型输出做静默 clamp。

## YOLO ROI 与 WideResNet50

- `roi_locator.backend: onnx_yolo` 使用 Dome 语义光源图，模型路径默认为 `model/roi_yolo/seat_roi_yolo.onnx`，输出行表与 ONNX detection rows 相同。
- `embedding_backend: onnx_wideresnet50` 使用 `model/wideresnet50/seat_wrn50_embedding.onnx`，输出一维向量，长度必须等于 `embedding_dim`。
- 两者都通过统一 ONNX Runtime 适配层创建 session。模型文件缺失、为空、依赖未安装、输入节点缺失或输出为空都会返回保守错误。

## 候选融合

模型候选进入规则引擎前会先经过 `FusionEngine`：

```yaml
fusion:
  iou_threshold: 0.5
  class_aware: true
  max_candidates_per_roi: 16
```

- 默认按 `(camera_id, roi_name, class_name)` 分组做 IoU NMS。
- `class_aware: false` 时，同一机位同一 ROI 的不同类别候选也会互相压制。
- 重叠候选会保留最高分 bbox 和分数，并合并 `evidence_lights`。
- 每个 ROI 最多保留 `max_candidates_per_roi` 个候选，超出的低分候选会被压制。
- trace 的 `fusion_summary` 会记录输入候选数、输出候选数和压制数量。
