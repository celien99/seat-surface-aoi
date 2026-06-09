# Python 检测算法模块规范

## 模块定位

`python_detector` 是独立的算法模块，只负责从 `SeatInspectionJob` 到 `InspectionResult` 的检测链路：

```text
SeatInspectionJob
  -> ImageQualityGate
  -> Preprocessor
  -> ReflectanceCubeBuilder
  -> FeatureBuilder
  -> InferenceEngine
  -> FusionEngine
  -> RuleEngine
  -> InspectionResult
```

PLC、相机、频闪、触发时序和共享内存数据面由 C++ 主控负责。Python 模块可以读取共享内存任务和写回结果，但不能控制现场设备。

## 公开入口

- `python_detector.SeatSurfaceAoiAlgorithm`：纯算法入口，适合回放、测试、离线验证和嵌入式调用。
- `python_detector.InspectionPipeline`：流水线编排入口，适合单元测试和替换子模块。
- `python_detector.detector_main`：在线检测进程入口，只负责 IPC 循环和结果发布。
- `seat-aoi-detector`：安装包后提供的命令行入口，等价于 `python3 -m python_detector.detector_main`。

业务代码优先依赖公开入口；跨子包调用内部类时，应保持单向依赖：

```text
config / ipc data types
  -> pipeline pure logic
  -> models runtime adapters
  -> algorithm facade
  -> detector_main IPC process
```

## 依赖分组

项目根目录 `pyproject.toml` 是 Python 算法层的依赖与工具配置入口：

- 默认依赖：`PyYAML`，用于配方、标定和 ROI YAML。
- `test`：`pytest`，用于算法层单元测试。
- `onnx`：`numpy`、`onnxruntime`，仅在启用 ONNX/YOLO/WideResNet50 后端时需要。
- `dev`：`pytest`、`ruff`，用于本地开发验证。

默认 fake/statistical/PatchCore exact KNN 参考链路不依赖 ONNX Runtime 或 FAISS。缺少可选后端依赖时必须返回 `RECHECK` 或 `ERROR`，不能输出 `OK`。

## 路径规范

默认配方、标定和 ROI 模板从包内 `python_detector/config` 加载，不依赖当前工作目录。仓库相对路径如 `python_detector/config/roi/default_roi.yaml` 仍保留兼容，用于已有配置和测试机脚本。

## 代码规范

- 新增算法逻辑放在 `python_detector/pipeline`、`python_detector/models` 或 `python_detector/config` 对应层，不在 `detector_main.py` 中堆业务逻辑。
- 纯图像/特征函数不要读写共享内存、文件系统或全局配置。
- 模型后端必须通过统一接口返回 `DefectCandidate`，后端异常必须被包装为保守错误。
- 坐标字段必须显式命名，例如 `bbox_xyxy_pixel`。
- 每个关键输入必须校验 shape、dtype、stride、光源、机位、时间戳和配方引用。
- 新增配置字段必须同步 schema、默认配方、测试和 README。
- 修改共享内存协议时必须同步 C++、Python、协议校验工具和相关测试。

## 验证命令

```bash
python3 -m pytest
python3 -m tools.validate_protocol
bash tools/run_simulated_ipc.sh
```
