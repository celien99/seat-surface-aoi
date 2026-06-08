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
- `timings.json`

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

