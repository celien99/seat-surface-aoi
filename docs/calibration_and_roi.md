# 标定与 ROI 说明

## 文件位置

默认标定：

```text
python_detector/config/calibration/<camera_id>/simulated_v1.yaml
```

默认 ROI：

```text
python_detector/config/roi/default_roi.yaml
```

## 校验规则

- 图像 `calibration_id` 必须和配方/标定文件一致。
- 图像尺寸必须和标定文件一致。
- ROI 至少包含 3 个点。
- 配方声明的 `roi_template` 文件必须存在，不允许静默回退到标定文件内置 ROI。
- ROI 点必须是整数 `[x, y]`，不能重复，多边形面积必须大于 0。
- `output_size` 必须是两个大于 0 的整数。
- `light_alignment.<light>.matrix_3x3` 必须包含 9 个有限数字。
- 轴对齐 4 点矩形 ROI 会走快速裁剪；`output_size` 必须等于该矩形外接框尺寸。
- 非轴对齐 ROI 必须提供 4 个点，Python 预处理会按四点透视展开到 `output_size`。
- 透视展开只支持当前在线主链路的 `MONO8` ROI 输入；输出图会记录 `roi_to_source_matrix` 和 `source_to_roi_matrix`，用于把模型 ROI 局部 bbox 映射为原图 `bbox_xyxy_pixel`，以及把原图 bbox 映射回 ROI 图生成 overlay。
- `LightFrame.bbox_xyxy_pixel` 表示 ROI 在原图中的外接框；轴对齐 ROI 等于裁剪框，倾斜 ROI 等于四点透视区域的原图外接框。
- `base_light_id` 缺失时按配方 fallback；fallback 也缺失时返回 `RECHECK/ERROR`。
- 生产环境默认策略应为全局 ROI 定位 + ROI 局部对齐；固定 identity 或全局单应矩阵只适用于当前模拟环境、标准样件验证或刚性夹具条件。
- ROI 定位失败、局部对齐误差超过配方阈值、ROI 模板缺失或坐标越界时不能输出 `OK`。

## V4 Dome ROI 定位

配方 `roi_locator.dome_semantic_light` 默认使用 `DOME`，再通过 `v4_lights.semantic_to_light_id` 映射到实际 light id，例如默认 `DOME -> DIFFUSE`。

支持三类 ROI 定位后端：

- `template`：使用 ROI 模板，适用于模拟、夹具稳定样件或兜底验证。
- `fake_yolo`：按模板生成可追溯 YOLO 行，用于测试 YOLO 输出到 ROI 模板坐标系的转换链路。
- `onnx_yolo`：读取 Dome ROI 图，调用 ONNX 模型，解析 `[x1, y1, x2, y2, score, class_id]`。

`class_id` 按 `roi_locator.class_names` 映射到 ROI 名称。置信度不足、姿态误差超过 `max_pose_error_px`、bbox 越界、Dome 图缺失或类别未映射时返回 `RECHECK`。

## ECC 配准

`registration.method: fixed_calibration` 使用标定矩阵检查角点误差。

`registration.method: ecc` 使用在线 ROI 平移搜索参考实现，输出每个光源相对 `base_light_id` 的矩阵、平移量、相关系数、迭代次数、收敛状态和误差。配准通过时，非基准光源 ROI 会按估计平移重采样到基准光源坐标，再进入多光源特征构建；trace 详情中的 `applied` 标记会记录该位移是否已应用。相关系数低于 `min_correlation`、ROI 尺寸不一致或误差超过 `quality.max_registration_error_px` 时返回 `RECHECK`。

## 测试机更新标定流程

1. 按机位生成独立标定文件。
2. 更新配方中的 `calibration_id` 和 `roi_template`。
3. 用标准样件验证全局 ROI 定位、局部对齐、ROI 边界、清晰度、曝光和多光源对齐。
4. 标定变更必须形成 commit，并同步更新 README 或相关 docs。

## 缓存边界

Python 检测进程会缓存标定解析结果，缓存 key 包含 `camera_id`、`calibration_id` 和 ROI 模板路径。同一标定文件搭配不同 ROI 模板时会分别加载，避免多 SKU 或多 ROI 版本共用同一个 Python 进程时复用错误 ROI。
