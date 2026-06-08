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
- `error.json`
- `images/<camera_id>/<roi_name>/<light_id>.pgm`
- `overlays/*.ppm`（存在缺陷时生成）

图像追溯使用无依赖的 Netpbm 格式：

- `.pgm` 保存 ROI 单光源灰度图，像素来自预处理后的 MONO8 ROI。
- `.ppm` 保存缺陷 overlay，在 ROI 图上以红色框绘制 `bbox_xyxy_pixel` 通过 `source_to_roi_matrix` 映射后的缺陷框；轴对齐 ROI 会退化为普通平移映射。
- `error.json` 保存检测流水线或模型异常的类型和消息；无异常时为空对象。
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
- `trace.save_ok_ratio: 0` 表示不保存 OK，`1.0` 表示全量保存 OK，中间值按 `recipe_id`、`sku`、`seat_id`、`sequence_id` 和 `trigger_id` 做确定性哈希抽样。
- 同一批任务重复回放时 OK 抽样结果保持稳定，便于对比模型版本和配方版本。
