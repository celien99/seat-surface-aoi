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

## 失败策略

配方缺失、格式错误、关键光源缺失、模型后端不支持时，检测进程必须返回 `RECHECK` 或 `ERROR`，不能使用默认 OK 兜底。

## 测试机新增 SKU 流程

1. 复制默认配方。
2. 修改 SKU、机位启用列表和光源顺序。
3. 修改质量阈值和缺陷阈值。
4. 指向对应标定文件和 ROI 模板。
5. 使用 `python3 -m pytest python_detector/tests` 验证 schema。
6. 使用 replay 工具回放样本。

