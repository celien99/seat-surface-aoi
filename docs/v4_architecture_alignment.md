# V4.0 架构对齐说明

本文以 `docs/assets/architecture-v4.png` 中的「汽车座椅表面缺陷检测系统整体架构图（V4.0 方案）」作为目标架构，说明当前仓库已经实现的能力、已验证的边界和后续需要补齐的模块。

![汽车座椅表面缺陷检测系统整体架构图 V4.0](assets/architecture-v4.png)

## 总体判断

当前项目已经具备 V4.0 架构的主干工程骨架：

- C++ 作为实时主控，负责 PLC、相机、光源、触发同步、共享内存写入、结果读取和保守降级。
- Python 作为独立检测进程，负责检测链路，不参与 PLC、相机和频闪控制。
- 在线图像与结果通过 POSIX 共享内存传输，不使用 TCP。
- 协议错误、CRC 错误、缺帧、超时、质量门禁失败和模型异常都不会输出 `OK`。

但当前实现还不是完整 V4.0 算法方案。项目目前更接近「可验证的工业 AOI 参考骨架 + 基础算法流水线」，而不是已经完成的 PatchCore 异常检测生产算法。

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
- C++ 模拟链路支持 `camera_exposure_output` 硬触发同步模式。
- 当前硬件 SDK 接入仍是可替换接口和模拟驱动，不是完整真实产线驱动。

差距：

- 光源命名和图中 `Dome`、`DarkField-L/R`、`BrightField` 语义尚未完全统一。
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
- 当前 ROI 来源是标定/模板，不是 YOLO 在线定位。

差距：

- 需要增加 Dome 图 ROI detector。
- 需要定义 YOLO 输出到 ROI 模板/坐标系的转换规则。
- ROI 定位失败、置信度不足、姿态超差必须返回 `RECHECK`。

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

差距：

- 当前方法是固定标定矩阵检查，不是 ECC 在线配准。
- 需要实现 ECC 配准策略、最大迭代次数、收敛阈值、失败策略和配准质量报告。

#### 3.3 特征提取

架构图要求：

- 使用共享特征提取网络，例如 WideResNet50。
- 分别提取 Dome、DarkField-L、DarkField-R、BrightField 等特征。

当前状态：

- 已构建多光源手工特征，包括 diffuse、polar diffuse、high left/right、high max-min、可选 low dark 差分、局部对比和高光抑制特征。
- 已具备 ONNX 推理入口和 fake 后端。

差距：

- 需要实现 WideResNet50 或等价 embedding 提取后端。
- 需要明确各光源输入归一化、特征层选择、embedding 维度和批处理策略。

#### 3.4 特征融合与降维

架构图要求：

- 多光源特征 concat。
- 使用 PCA 降维。
- 输出 unified embedding。

当前状态：

- 已能按模型配置生成 NCHW tensor。
- 已记录 feature summary 和 evidence lights。

差距：

- 尚未实现统一 embedding 对象。
- 尚未实现 PCA 训练参数加载、投影、版本校验和维度校验。

#### 3.5 PatchCore 异常检测

架构图要求：

- 训练阶段构建 memory bank。
- 使用正常样本特征，执行 coreset subsampling。
- 推理阶段输入 unified embedding。
- 使用 FAISS/KNN 加速近邻搜索。
- 输出 anomaly score。

当前状态：

- 配方 schema 已允许 `patchcore` 模型族，并限制只能作为 `safety_net`。
- 当前实际推理后端只有 `fake` 和 `onnx`。

差距：

- 需要实现 PatchCore 训练工具。
- 需要保存 memory bank、coreset 参数、PCA 参数和版本元数据。
- 需要实现 FAISS/KNN 推理后端。
- 需要定义 anomaly score 到 `OK`、`RECHECK`、`NG` 的阈值策略。

### 4. 后处理与决策层

架构图要求：

- 缺陷过滤与分类。
- 规则引擎。
- OK/NG 判定、可视化、报警输出、MES 系统对接。

当前状态：

- 已实现候选融合/NMS。
- 已实现类别阈值、面积阈值、`OK`、`NG`、`RECHECK`、`ERROR` 判定。
- 已支持 trace、ROI 图和缺陷 overlay。

差距：

- 缺陷过滤分类器仍需独立模块化。
- MES、报警输出和可视化界面不是当前仓库完整实现。
- 多 ROI 关联规则需要按实际缺陷工艺继续扩展。

### 5. 系统管理与维护

架构图要求：

- 数据管理。
- 模型管理。
- 系统监控。

当前状态：

- 已有配方、标定、模型配置、trace、回放和 benchmark 文档。
- 已有模型缓存隔离、trace 保存策略和测试机集成清单。

差距：

- 尚未实现完整数据平台、模型版本平台和系统监控服务。
- 现场运行指标、健康检查和报警面板仍需结合部署环境建设。

## 推荐补齐顺序

1. 统一 V4.0 光源命名与配方字段，明确 `DOME`、`DARKFIELD_L`、`DARKFIELD_R`、`BRIGHTFIELD` 到当前 light id 的映射。
2. 增加 Dome 图 YOLO ROI 定位后端，并保留模板 ROI 作为模拟和兜底模式。
3. 实现 ECC ROI 配准模块，输出配准矩阵、收敛状态和误差指标。
4. 增加 WideResNet50 embedding 后端，定义多光源特征层和统一 embedding 数据结构。
5. 增加 PCA 参数加载、版本校验和投影模块。
6. 实现 PatchCore memory bank 构建、coreset subsampling 和 FAISS/KNN 推理。
7. 将 anomaly score 接入规则引擎，并补齐 `RECHECK` 优先的阈值策略。
8. 扩展 trace，使 ROI 定位、ECC、embedding、PCA、KNN 和 anomaly score 全链路可追溯。
9. 接入真实相机、频闪和 PLC 后做节拍、稳定性和故障注入压测。

## 当前验证命令

```bash
python3 -m pytest python_detector/tests
python3 -m tools.validate_protocol
bash tools/run_simulated_ipc.sh
```

当前仓库要求上述验证在代码变更后通过。文档变更通常至少需要检查 Markdown 链接、图片路径和 git 状态。
