# 模型后端说明

## 当前后端

- `fake`：默认后端，用于模拟 OK、RECHECK、NG 分支。
- `onnx`：可选后端。未安装 `onnxruntime`、模型路径为空、模型文件不存在、输入 tensor 缺失、输出解码未配置或输出解析失败时，返回保守错误。

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

```yaml
models:
  fake_default:
    backend: fake
    fake_mode: auto
    model_family: supervised
    role: primary
  scratch_onnx:
    backend: onnx
    model_path: models/scratch.onnx
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
    backend: onnx
    model_path: models/patchcore_unknown.onnx
    model_family: patchcore
    role: safety_net
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
