# 追溯与回放说明

## 追溯目录

默认追溯根目录为 `trace/`。

一次追溯记录包含：

- `job.json`
- `result.json`
- `recipe_summary.json`
- `quality_report.json`
- `registration_report.json`
- `feature_summary.json`
- `fusion_summary.json`
- `timings.json`
- `images/<camera_id>/<roi_name>/<light_id>.pgm`
- `overlays/*.ppm`（存在缺陷时生成）

图像追溯使用无依赖的 Netpbm 格式：

- `.pgm` 保存 ROI 单光源灰度图，像素来自预处理后的 MONO8 ROI。
- `.ppm` 保存缺陷 overlay，在 ROI 图上以红色框绘制 `bbox_xyxy_pixel` 映射后的缺陷框。
- 如果配方默认不保存 OK，OK 样本不会落盘图像；`NG`、`RECHECK`、`ERROR` 按默认策略保存。

## 回放工具

```bash
python3 -m tools.replay_dataset --count 3 --write-trace
```

## Benchmark 工具

```bash
python3 -m tools.benchmark_pipeline --count 10
```

## 保存策略

- `RECHECK`、`ERROR`、`NG` 默认保存。
- `OK` 默认不保存，可通过配方 `trace.save_ok_ratio` 调整。
