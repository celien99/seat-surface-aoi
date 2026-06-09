# 配方设计说明

## 配方文件

默认配方位于 `python_detector/config/default_recipe.yaml`。

必须包含：

- `recipe_id`
- `sku`
- `light_order`
- `cameras`
- `quality`
- `registration`
- `thresholds`
- `models`
- `trace`

## V2 光源与模型标准

默认生产主链路使用 4 个必需光源：

```yaml
quality:
  required_lights:
    - DIFFUSE
    - POLAR_DIFFUSE
    - HIGH_LEFT
    - HIGH_RIGHT
```

`LOW_LEFT`、`LOW_RIGHT`、`LOW_FRONT`、`LOW_REAR`、`HIGH_FRONT`、`HIGH_REAR`、`NIR` 只能作为 ROI 增强光源。未声明依赖这些增强光源的 ROI，不能因为增强光源不存在而中断主流程。

## 质量门禁字段

`quality` 除了曝光、饱和、清晰度、运动模糊和配准阈值，还应配置采集一致性与光源稳定性阈值：

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

- `max_saturation_ratio` / `min_mean_gray` / `max_mean_gray`：单帧过曝、欠曝和亮度范围阈值。
- `min_sharpness`：基于有效像素区域的拉普拉斯清晰度下限。
- `min_motion_gradient`：基于水平/垂直方向梯度均值的运动模糊下限。
- `max_light_mean_delta`：同一机位必需光源均值灰度最大跨度，用于发现光源强度漂移或触发异常。
- `max_capture_span_us`：同一机位必需光源包的最大时间戳跨度。
- `max_exposure_delta_us` / `max_gain_delta`：必需光源之间曝光和增益允许差值。
- `require_monotonic_timestamps`：要求必需光源按配方顺序时间戳单调。
- `require_unique_frame_indices`：要求必需光源帧号不重复。

质量门禁还会在预处理前校验每帧图像元数据，当前在线主链路只接受 `MONO8`、`UINT8`、`MONO`、单通道图像；`stride_bytes` 必须大于等于有效行宽，图像长度必须覆盖完整 stride。元数据不满足要求时返回 `RECHECK`。

这些检查失败时返回 `RECHECK`，不能输出 `OK`。配方加载阶段会拒绝越界质量阈值，例如饱和比例不在 `[0, 1]`、灰度范围不在 `[0, 255]`、灰度上下限反向或运动/光源稳定性阈值为负。

ROI 模型使用两个层次：

- `model_key` / `roi_models`：主检测模型，必须引用 `role: primary` 的模型。
- `safety_net_model_key` / `roi_safety_net_models`：未知缺陷安全网，必须引用 `role: safety_net` 的模型。

PatchCore 只能作为 `safety_net`，不能通过 `model_key` 或 `roi_models` 成为主检测模型。

## 失败策略

配方缺失、格式错误、关键光源缺失、模型后端不支持时，检测进程必须返回 `RECHECK` 或 `ERROR`，不能使用默认 OK 兜底。

## 阈值安全校验

- `thresholds.<class>.ng_score` 和 `recheck_score` 必须在 `[0, 1]` 范围内。
- `recheck_score` 不能大于 `ng_score`，否则同一缺陷会出现复检阈值高于 NG 阈值的反向规则。
- `thresholds.<class>.min_area_px` 必须大于等于 0。
- `models.<model>.score_threshold` 必须在 `[0, 1]` 范围内。

这些配置错误会在配方加载阶段失败，不能进入在线判定链路。

## 测试机新增 SKU 流程

1. 复制默认配方。
2. 修改 SKU、机位启用列表和光源顺序。
3. 修改质量阈值和缺陷阈值。
4. 指向对应标定文件和 ROI 模板。
5. 使用 `python3 -m pytest python_detector/tests` 验证 schema。
6. 使用 replay 工具回放样本。
