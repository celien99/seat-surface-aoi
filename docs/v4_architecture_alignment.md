# V4.0 架构对齐说明

本文以 `docs/assets/architecture-v4.png` 中的「汽车座椅表面缺陷检测系统整体架构图（V4.0 集成 ONNX + FAISS 方案）」作为目标架构，说明当前仓库已经实现的能力、已验证的边界和后续需要补齐的模块。

![汽车座椅表面缺陷检测系统整体架构图 V4.0 集成 ONNX + FAISS 方案](assets/architecture-v4.png)

## 总体判断

当前项目已经具备 V4.0 架构的主干工程骨架：

- C++ 作为实时主控，负责 PLC、相机、光源、触发同步、共享内存写入、结果读取和保守降级。
- Python 作为独立检测进程，负责检测链路，不参与 PLC、相机和频闪控制。
- 在线图像与结果通过 POSIX 共享内存传输，不使用 TCP。
- Python AI Runtime 以 ONNX Runtime 作为 YOLO/WideResNet50/FilterClassifier 等模型的推理底座，PatchCore 向量检索优先使用 FAISS，缺索引或缺依赖时回退 exact KNN。
- 协议错误、CRC 错误、缺帧、超时、质量门禁失败和模型异常都不会输出 `OK`。

当前实现已经从「工业 AOI 参考骨架 + 基础算法流水线」推进到「V4.0 主要算法接口可验证参考链路 + 真实模型工程接入点」。真实产线仍需要接入设备 SDK、替换 `model/` 下的真实 YOLO/WideResNet50/PatchCore 产物、完成训练评估、MES/报警和监控平台。

## 分层对齐

### 1. 光学采集层

架构图要求：

- Dome 主光源。
- DarkField-L / DarkField-R。
- BrightField 可选。
- 其它光源可扩展。
- PLC -> C++ -> 相机/光源控制。
- GPIO/TTL 硬件触发，微秒级曝光，触发抖动小于 10 微秒。

当前状态：

- 已在 C++ 配置和 Python 配方中支持多光源顺序。
- 默认主链路使用 `DIFFUSE`、`POLAR_DIFFUSE`、`HIGH_LEFT`、`HIGH_RIGHT`。
- Python 配方通过 `v4_lights.semantic_to_light_id` 统一 V4 语义光源，默认映射为 `DOME -> DIFFUSE`、`DARKFIELD_L -> HIGH_LEFT`、`DARKFIELD_R -> HIGH_RIGHT`、`BRIGHTFIELD -> POLAR_DIFFUSE`。
- C++ 模拟链路支持 `camera_exposure_output` 硬触发同步模式。
- 当前硬件 SDK 接入仍是可替换接口和模拟驱动，不是完整真实产线驱动。

差距：

- 真实光源控制器、工业相机 SDK、PLC/编码器现场协议仍需按设备型号接入。
- 微秒级抖动指标需要真实硬件压测证明。

### 2. 控制与通信层

架构图要求：

- C++ 实时系统负责设备控制、触发管理、相机采集、光源切换、状态监控和日志记录。
- 通信机制使用共享内存传输多光源图像数据、触发信号/状态信息、时间戳和系统状态。

当前状态：

- `cpp_controller` 已实现 C++ 主控、PLC 抽象、相机 worker、光源控制器、触发调度、frame/result ring buffer。
- `python_detector` 通过共享内存读取任务并写回检测结果。
- C++ 侧会校验 `sequence_id`、`trigger_id`、`seat_id`、decision、质量状态、错误码和缺陷数量。
- detector 超时、slot 不可用、缺帧和协议异常会保守返回 `RECHECK` 或 `ERROR`。

结论：

- 该层与 V4.0 主体要求基本对齐。

### 3. 算法处理层

#### 3.1 ROI 定位

架构图要求：

- 仅使用 Dome 光源图。
- 通过 YOLO 目标检测模型输出座椅 ROI 检测结果。

当前状态：

- 已支持 ROI 模板加载、轴对齐矩形裁剪和四点多边形透视展开。
- 已增加 `RoiLocator`，支持 `template`、`fake_yolo` 和 `onnx_yolo` 后端。
- 已在根目录 `model/roi_yolo/seat_roi_yolo.onnx` 预留真实 ROI YOLO 产物路径，并提供 `production_model.example.yaml` 配方模板和 `tools.validate_model_assets` 校验。
- ROI 定位只读取 `DOME` 语义光源映射出的图像；YOLO 输出按 `[x1, y1, x2, y2, score, class_id]` 解码，并通过 `roi_locator.class_names` 映射到 ROI 模板。
- ROI 置信度不足、姿态误差超差、输出越界或缺 Dome 图会返回 `RECHECK`，不会输出 `OK`。

差距：

- 真实 YOLO ROI 权重、输入尺寸、类别训练集和评估报告仍需按现场数据产出；工程路径和校验入口已补齐。

#### 3.2 ROI 裁剪与图像配准

架构图要求：

- 输入所有光源图像。
- 以 Dome ROI 为参考图。
- DarkField-L/R、BrightField 等 ROI 与参考图对齐。
- 使用 ECC 图像配准，输出配准后 ROI。

当前状态：

- 已支持所有光源 ROI 裁剪。
- 已支持透视展开和 ROI 到原图的双向矩阵。
- 已使用标定文件中的 `light_alignment.matrix_3x3` 计算配准误差。
- 已支持 `registration.method: ecc`，通过 ROI 平移搜索输出 ECC 风格配准矩阵、相关系数、迭代次数、收敛状态和误差报告。

差距：

- 当前 ECC 为轻量参考实现，真实产线可替换为 OpenCV ECC 或设备侧高精度配准并继续复用相同报告字段。

#### 3.3 特征提取

架构图要求：

- 使用共享特征提取网络，例如 WideResNet50。
- 分别提取 Dome、DarkField-L、DarkField-R、BrightField 等特征。

当前状态：

- 已构建多光源手工特征，包括 diffuse、polar diffuse、high left/right、high max-min、可选 low dark 差分、局部对比和高光抑制特征。
- 已具备 ONNX 推理入口和 fake 后端。
- 已支持统计 embedding 参考后端和 `onnx_wideresnet50` embedding 入口，配置中可声明 embedding 版本、维度和特征层。
- 已在 `model/wideresnet50/seat_wrn50_embedding.onnx` 预留 WideResNet50 embedding 产物路径；占位文件未替换时会被模型资产校验和运行时保守拒绝。

差距：

- 真实 WideResNet50 权重、输入归一化、特征层选择和批处理策略仍需按训练产物确认；工程路径和运行时校验已补齐。

#### 3.4 特征融合与降维

架构图要求：

- 多光源特征 concat。
- 使用 PCA 降维。
- 输出 unified embedding。

当前状态：

- 已能按模型配置生成 NCHW tensor。
- 已记录 feature summary 和 evidence lights。
- 已实现 unified embedding summary。
- 已实现 PCA JSON 参数加载、版本校验、输入/输出维度校验和投影。

差距：

- PCA 参数训练与版本发布仍需纳入离线模型管理流程。

#### 3.5 PatchCore 异常检测

架构图要求：

- 训练阶段构建 memory bank。
- 使用正常样本特征，执行 coreset subsampling。
- 推理阶段输入 unified embedding。
- 使用 FAISS/KNN 加速近邻搜索。
- 输出 anomaly score。

当前状态：

- 配方 schema 允许 `patchcore` 模型族，并限制只能作为 `safety_net`。
- 已支持 `patchcore_knn` 后端，读取 memory bank JSON，执行 exact KNN，输出 anomaly score。
- 已提供 `tools.build_patchcore_memory_bank`，支持从 JSONL embedding 构建 memory bank 并保存 coreset 参数、PCA 版本和 FAISS 元数据。
- 已支持 `faiss_index_path`，部署环境有有效 FAISS 索引时优先使用 FAISS；缺索引或缺依赖时回退 exact KNN，并在 trace 中记录 `backend` 与 `fallback_reason`。
- 已在 `model/patchcore/` 预留 PCA、memory bank 和 FAISS 索引产物路径，并提供模型资产校验工具。
- anomaly score 会作为 `unknown_anomaly` 候选进入融合、缺陷过滤和规则引擎，低置信但可疑样本走 `RECHECK`。

差距：

- FAISS 索引文件仍需由部署环境基于真实 memory bank 生成并验证延迟、内存占用和回退行为。
- 正常样本库、coreset 策略和阈值曲线仍需通过现场数据训练与验证。

### 4. 后处理与决策层

架构图要求：

- 缺陷过滤与分类。
- 规则引擎。
- OK/NG 判定、可视化、报警输出、MES 系统对接。

当前状态：

- 已实现候选融合/NMS。
- 已将缺陷过滤抽为 `DefectFilter`，便于后续接入二阶段分类器或工艺过滤规则。
- 已实现类别阈值、面积阈值、`OK`、`NG`、`RECHECK`、`ERROR` 判定。
- 已支持 trace、ROI 图和缺陷 overlay。

差距：

- MES、报警输出和可视化界面不是当前仓库完整实现。
- 多 ROI 关联规则需要按实际缺陷工艺继续扩展。

### 5. 系统管理与维护

架构图要求：

- 数据管理。
- 模型管理。
- 系统监控。

当前状态：

- 已有配方、标定、模型配置、trace、回放和 benchmark 文档。
- 已有根目录 `model/` 模型产物占位、真实模型配方模板和 `tools.validate_model_assets` 上线前资产校验。
- 已有模型缓存隔离、trace 保存策略和测试机集成清单。
- trace 已扩展 ROI 定位、ECC、embedding、PCA、KNN 和 anomaly score 摘要。

差距：

- 尚未实现完整数据平台、模型版本平台和系统监控服务。
- 现场运行指标、健康检查和报警面板仍需结合部署环境建设。

### 6. AI Runtime 与依赖

架构图要求：

- AI Runtime 使用 ONNX 作为推理底座，承载 YOLOvX ROI 定位、WideResNet50 特征提取和 FilterClassifier 缺陷过滤分类等模型。
- 向量检索引擎使用 FAISS，支持 CPU/GPU、IndexFlatL2、IVF、PQ 等部署选择。
- 基础依赖包括 OpenCV、NumPy、共享内存 SDK 和图像处理组件。

当前状态：

- 已提供统一 ONNX Runtime 适配层，ROI YOLO、通用 ONNX detection rows 和 WideResNet50 embedding 共享 session 创建、输入构建和保守错误处理。
- 已在 `model/` 目录预留 YOLO ROI、监督缺陷检测、WideResNet50 embedding、PCA、PatchCore memory bank 和 FAISS 索引产物路径。
- `pyproject.toml` 已提供 `onnx` 和 `faiss` optional extras；默认模拟链路不强制安装 ONNX Runtime 或 FAISS。
- PatchCore 在线链路配置 `faiss_index_path` 后优先尝试 FAISS，失败时回退 exact KNN，并在 trace 中记录 `backend` 与 `fallback_reason`。
- Python 层当前只负责检测算法，不控制 PLC、相机或频闪。

差距：

- 真实 ONNX 模型、FAISS 索引、OpenCV 高精度 ECC 后端和现场性能参数仍需部署环境实测确认。
- GPU 推理、FAISS GPU 索引和平台化依赖管理仍需结合产线硬件规格建设。

## 推荐补齐顺序

1. 接入真实相机、频闪、PLC/编码器和光源控制器 SDK，并做节拍、稳定性和故障注入压测。
2. 训练并接入真实 Dome YOLO ROI 定位权重，固化 ROI 类别、置信度、姿态误差和复检阈值。
3. 用现场数据验证 ECC 参数，必要时替换为 OpenCV ECC 或更高精度配准后端。
4. 接入真实 WideResNet50 embedding 权重，固化输入归一化、特征层、embedding 维度和批处理策略。
5. 基于正常样本训练 PCA 与 PatchCore memory bank，替换 `model/patchcore/` 占位产物，产出阈值曲线和按缺陷类别/ROI/材质/颜色的评估报告。
6. 在部署环境生成并接入 FAISS 加速索引，验证 KNN 延迟、内存占用和 exact KNN 回退。
7. 按现场硬件规格固化 ONNX Runtime、FAISS、OpenCV 和 NumPy 版本，完成 AI Runtime 性能基准。
8. 按现场工艺扩展多 ROI 关联规则、MES/报警接口、数据平台、模型版本平台和系统监控服务。

## 当前验证命令

```bash
python3 -m pytest python_detector/tests
python3 -m tools.validate_protocol
python3 -m tools.validate_model_assets --recipe production_model_example
bash tools/run_simulated_ipc.sh
```

默认模拟链路要求测试、协议校验和模拟 IPC 通过；`validate_model_assets --recipe production_model_example` 在占位文件未替换时应失败，并列出需要替换的真实模型产物。
