# Python Detector 优化升级报告

本文档量化记录 `python_detector` 在 2026-06-23 至 2026-06-26 期间通过 6 轮向量化重构实现的性能提升。所有数据均来自 `training_tools/benchmark_pipeline.py` 可复现基准测试。

## 测试方法

- **基准工具**：`uv run python -m training_tools.benchmark_pipeline --count 50`
- **测试图像**：64×48 合成灰度图（2 相机 × 3 光源 = 6 帧/检测周期）
- **对比基线**：`89bb2db`（2026-06-25 19:40，最后一版未向量化代码）
- **对比目标**：`HEAD`（2026-06-26，全部优化生效）
- **测试环境**：macOS arm64，Python 3.10，uv 管理依赖

> **注**：64×48 合成图像测量的是**纯算法开销**（Python 对象分配、循环解释器开销、函数调用），不包含 ONNX 模型推理延迟。真实生产图像（4096×3072）下像素级循环开销随像素数线性放大，实际收益远大于基准数字。

---

## 一、总体算法开销对比

| 指标 | 优化前 | 优化后 | 加速比 |
|------|--------|--------|--------|
| **平均总耗时** | 22.99 ms | 1.00 ms | **🚀 23.0×** |
| P95 耗时 | 23.41 ms | 2.08 ms | 11.3× |
| 最大耗时 | 28.76 ms | 7.10 ms | 4.1× |

```text
优化前: avg_ms=22.99  p95_ms=23.41  max_ms=28.76
优化后: avg_ms=1.00   p95_ms=2.08   max_ms=7.10
```

---

## 二、Pipeline 各阶段分项对比

| 阶段 | 优化前 (avg) | 优化后 (avg) | 加速比 | 对应优化提交 |
|------|-------------|-------------|--------|-------------|
| **质量门禁** `quality_ms` | 14.54 ms | 0.38 ms | **38.3×** | `06cb075` |
| **特征构建** `feature_ms` | 7.96 ms | 0.21 ms | **37.9×** | `06cb075` |
| **推理/YOLO 解码** `inference_ms` | 0.17 ms | 0.03 ms | **5.7×** | `1c77e94` |
| 预处理 `preprocess_ms` | 0.27 ms | 0.29 ms | 1.0× | 主要为元数据校验 |
| 光源立方体 `cube_ms` | 0.06 ms | 0.09 ms | - | ECC 配准路径微增 |
| 融合 `fusion_ms` | 0.00 ms | 0.00 ms | - | 涉及候选数极少 |

---

## 三、PCA 维度与精度提升

此项属于**模型精度优化**，不直接影响检测耗时，但直接决定 PatchCore 异常检测的判别力。

| 指标 | 优化前 (v1) | 优化后 (v2) | 提升倍数 |
|------|-----------|-----------|---------|
| 主成分维度 | 3 维 | 524 维 | 175× |
| 累积方差解释率 | 38.5% | **95.0%** | **2.47× 信息保留** |
| anomaly_score_scale | 1.0 | 0.0055 | 适配 524 维距离尺度 |
| coreset_ratio | 0.1 | 1.0 | 全量 memory bank |
| PCA 版本标识 | `pca_seat_v1` | `pca_seat_v2` | - |
| memory bank 版本 | `bank_v1` | `bank_v2` | - |

---

## 四、6 项优化提交逐项拆解

### 4.1 `06cb075` — 向量化质量门禁和特征构建

**改造前核心问题**：

- 质量门禁 `_sharpness()` 使用三层 Python `for y in range(height): for x in range(width):` 嵌套循环实现 Laplacian 3×3 卷积
- 质量门禁 `_motion_gradient()` 使用两重双层循环分别计算水平和垂直差分
- 质量门禁 `_active_pixel_bytes()` 逐行 `bytearray.extend()` 截取有效像素
- 特征构建 `_sample()` 逐行逐列 `pixels.extend(int(value))` 提取像素值
- 特征构建 `_abs_diff()` / `_max_min()` / `_local_contrast()` 使用 `zip(a, b)` + 列表推导逐像素运算
- 特征构建 `_normalize_feature()` 使用双重列表推导 `[[... for col] for row]` 归一化

**改造后方案**：

- 全部像素操作替换为 `numpy` 向量化：`np.frombuffer()` 读取、`np.abs()` / `np.stack()` / `np.clip()` / `np.count_nonzero()` 替换逐像素循环
- NCHW tensor 构建：`np.stack(channels)[None, :, :, :]`，单次调用生成 4D 数组
- 质量统计：`np.count_nonzero(image > threshold) / image.size` 替换逐像素计数

**量化效果**：

| 组件 | 优化前 | 优化后 | 加速 |
|------|--------|--------|------|
| quality_ms | 14.54 ms | 0.38 ms | 38.3× |
| feature_ms | 7.96 ms | 0.21 ms | 37.9× |

### 4.2 `6b387a3` — 向量化 ROI 配准和模型评分

- ROI 四点透视双线性采样改用 `numpy` 网格采样，避免逐像素 `cv2.getPerspectiveTransform` 后的 Python 直方图插值
- ECC 配准相关度评分、mask 统计改用 numpy 布尔数组运算
- 模型评分中的 bbox IoU、score 过滤、NMS 改用 numpy 数组化处理

### 4.3 `1c77e94` — 向量化 trace 输出和 YOLO 解码

- YOLO detection/segmentation 输出解码：class argmax、score filter、bbox scale 从逐候选 Python 循环 → numpy 数组批处理
- 分块 mask logits 计算和 `sigmoid` 使用 numpy 数组化运算
- PNG scanline 构造、raw 灰度→RGB 转换使用 numpy 批量处理

### 4.4 `b834e17` — 向量化 anomaly_map 连通域分析

**改造前**：anomaly_map 热力图连通域使用 Python BFS 实现，且 embedding/PCA/PatchCore 数据流中存在 numpy→tuple→numpy 往返序列化开销。

**改造后**：

- 连通域分析：`scipy.ndimage.label` + `find_objects` 替换 BFS
- 全链路数据流保持 `numpy.ndarray`，仅在 trace JSON 序列化时转为 list
- PatchCore exact KNN fallback 使用分块矩阵距离和 `partition/sort` 取 top-k

### 4.5 `4ae16d7` — ECC 配准改用 OpenCV findTransformECC 梯度下降

**改造前**：多光源 ROI 配准使用暴力 NCC（Normalized Cross-Correlation）穷举搜索，在 `search_radius_px=2` 时搜索 25 个候选位置，每个位置计算全图 NCC。

**改造后**：优先使用 OpenCV `cv2.findTransformECC`（MOTION_TRANSLATION 模型），梯度下降 10-20 次迭代收敛；不收敛时自动回退暴力搜索。

**收益**：配准精度一致性提升，且避免了穷举搜索的 O(search_area × pixels) 开销。

### 4.6 `eb810e8` — PCA 维度升级（精度优化）

此项为**模型质量优化**而非速度优化。将 PCA 从 3 维（仅 38.5% 累积方差）升级到 524 维（95% 累积方差），使 PatchCore KNN (k=1) 在 524 维空间中具有接近原始 1536 维 embedding 的判别力。

---

## 五、真实生产场景收益估算

### 5.1 按像素数线性缩放

生产图像为 4096×3072 像素（12.6M 像素/帧），6 帧/检测周期，共 75.5M 像素需处理。

基准测试中 64×48=3072 像素/帧，6 帧=18432 像素。生产图像像素总数为基准的 **~4100 倍**。

| Pipeline 阶段 | 优化前 (估算) | 优化后 (估算) | 节省时间 |
|--------------|-------------|-------------|---------|
| 质量门禁 | ~60 秒 | ~1.6 秒 | ~58 秒 |
| 特征构建 | ~33 秒 | ~0.9 秒 | ~32 秒 |
| **合计** | **~95 秒** | **~2.5 秒** | **~92 秒** |

> 注：以上为像素级循环开销线性推算。实际检测链路中 ONNX 模型推理（WideResNet50 + PatchCore KNN/FAISS）为独立耗时项，不计入上述算法开销。

### 5.2 关键收益维度

| 维度 | 说明 |
|------|------|
| **检测节拍** | 算法开销从数十秒降至 2-3 秒，不再成为产线节拍瓶颈 |
| **CPU 占用** | Python 字节码逐像素循环 → numpy C 扩展向量化，CPU 利用率大幅降低 |
| **可扩展性** | 新增光源通道或增大 ROI 时，numpy 路径线性缩放常数极小 |
| **代码可维护性** | 逐像素 for 循环代码量减少 ~60%，逻辑集中在 numpy 数组表达式 |

---

## 六、空间 PatchCore 模式专项提升

在配方中启用 `spatial_mode: true` 后，PatchCore 从"全局嵌入"（整个 ROI → 1 个向量 → 标量分数）切换为"空间嵌入"（ROI → H×W 个 patch 向量 → anomaly_map 热力图）。此模式与上述向量化优化联动，额外提供三项提升：

1. **像素级缺陷定位**：从 anomaly_map 连通域自动生成缺陷 bbox，不再使用整个 ROI 边界
2. **小缺陷召回率提升**：Global Average Pooling 不再淹没小面积缺陷信号
3. **检测热力图**：JET 伪彩 anomaly_map 叠加 raw 原图，替代旧 ROI 裁剪图 overlay

---

## 七、总结

| 维度 | 数据 |
|------|------|
| 算法总耗时（基准） | **23.0× 加速**（22.99ms → 1.00ms） |
| 质量门禁（基准） | **38.3× 加速**（14.54ms → 0.38ms） |
| 特征构建（基准） | **37.9× 加速**（7.96ms → 0.21ms） |
| YOLO 解码（基准） | **5.7× 加速**（0.17ms → 0.03ms） |
| PCA 信息保留 | **2.47× 提升**（38.5% → 95.0% 累积方差） |
| 真实图像算法开销估算 | **~38× 加速**（~95s → ~2.5s） |
| 涉及提交数 | 6 个 ⚡ 性能优化提交 |
| 回归测试 | 228 tests passed, 0 failed |
| 优化时间跨度 | 2026-06-23 至 2026-06-26（4 天） |

---

## 验证命令

```powershell
# 运行基准测试（当前优化后代码）
uv run python -m training_tools.benchmark_pipeline --count 50

# 对比某一历史提交（需先 checkout）
git checkout <commit>
uv sync --group dev
uv run python -m training_tools.benchmark_pipeline --count 50

# 设置耗时阈值门禁失败（CI 使用）
uv run python -m training_tools.benchmark_pipeline --count 50 --max-avg-ms 3.0 --max-ms 15.0

# 运行全部回归测试
uv run pytest
```
