# 追溯与回放说明

## 追溯目录

默认追溯根目录为 `trace/`。

一次追溯记录包含：

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
- `overlays/*.ppm`（存在缺陷时生成）

图像追溯使用无依赖的 Netpbm 格式：

- `.pgm` 保存 ROI 单光源灰度图，像素来自预处理后的 MONO8 ROI。
- `.ppm` 保存缺陷 overlay，在 ROI 图上以红色框绘制 `bbox_xyxy_pixel` 通过 `source_to_roi_matrix` 映射后的缺陷框；轴对齐 ROI 会退化为普通平移映射。
- `roi_location_report.json` 保存 Dome ROI 定位后端、Dome 映射光源、ROI 置信度、姿态误差和定位来源。
- `registration_report.json` 在 `method: ecc` 时包含每个非基准光源的矩阵、平移量、相关系数、迭代次数、收敛状态和误差。
- `feature_summary.json` 包含 tensor shape、输入通道、evidence lights，以及 PatchCore safety net 的 `embedding_summary`、`pca_summary` 和 `anomaly_summary`。
- `error.json` 保存检测流水线或模型异常的类型和消息；无异常时为空对象。
- 如果配方默认不保存 OK，OK 样本不会落盘图像；`NG`、`RECHECK`、`ERROR` 按默认策略保存。

## 回放工具

```bash
python3 -m tools.replay_dataset --count 3 --write-trace
```

回放输出包含 `sequence_id`、`decision`、`quality_pass`、`error_code`、缺陷数量和总耗时。存在质量门禁失败时，会追加 `quality_reasons` 摘要；存在流水线异常时，会追加 `error` 摘要；启用 `--write-trace` 且策略允许保存时，会追加 `trace_dir`。

可用 `--summary-limit` 控制每条结果最多输出的质量原因数量：

```bash
python3 -m tools.replay_dataset --count 3 --summary-limit 2
```

## Benchmark 工具

```bash
python3 -m tools.benchmark_pipeline --count 10
```

Benchmark 输出总耗时的平均值、p95、最大值，以及 `quality_ms`、`preprocess_ms`、`cube_ms`、`feature_ms`、`inference_ms`、`fusion_ms`、`total_ms` 等可用步骤的平均和最大耗时。

可配置性能阈值，超过阈值时命令返回 `2`，用于 CI 或现场版本回归检查：

```bash
python3 -m tools.benchmark_pipeline --count 20 --max-avg-ms 80 --max-ms 120 --max-step-ms quality_ms=10 --max-step-ms inference_ms=30
```

## 保存策略

- `RECHECK`、`ERROR`、`NG` 默认保存。
- `OK` 默认不保存，可通过配方 `trace.save_ok_ratio` 调整。
- `trace.save_ok_ratio: 0` 表示不保存 OK，`1.0` 表示全量保存 OK，中间值按 `recipe_id`、`sku`、`seat_id`、`sequence_id` 和 `trigger_id` 做确定性哈希抽样。
- 同一批任务重复回放时 OK 抽样结果保持稳定，便于对比模型版本和配方版本。
