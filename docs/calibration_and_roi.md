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
- `base_light_id` 缺失时按配方 fallback；fallback 也缺失时返回 `RECHECK/ERROR`。

## 测试机更新标定流程

1. 按机位生成独立标定文件。
2. 更新配方中的 `calibration_id` 和 `roi_template`。
3. 用标准样件验证 ROI 边界、清晰度、曝光和多光源对齐。
4. 标定变更必须形成 commit，并同步更新 README 或相关 docs。

