# PySide6/QML 展示前端

`display_app/` 是从 `/Users/yyh/code/online-detection-app` 迁移出的产线展示层，只负责读取当前项目 Python detector 与 C++ 主控生成的只读事件文件，显示线上检测、采样和复核状态。

它不会初始化相机、PLC、频闪、机器人或 `seat_defect_core`，也不会直接读写 C++/Python 在线共享内存。在线主链路仍然是：

```text
C++ 主控 -> 共享内存 Frame Ring -> Python detector -> 共享内存 Result Ring -> C++ 主控
                                      |
                                      v
                         trace/display_latest.json
                         trace/display_events.jsonl

                         C++ 主控 -> trace/cpp_controller_events.jsonl
                                      |
                                      v
                             PySide6/QML 展示前端
```

## 启动

```powershell
uv sync --extra display
uv run seat-aoi-display --trace-root trace --line-id AOI-1
```

开发时也可以直接运行：

```powershell
uv run python -m display_app.main --trace-root trace --poll-ms 300
```

`--trace-root` 必须指向 detector 输出 `display_latest.json` 的目录。默认使用根目录 `trace/`。

## 当前页面

- 监控：复用迁移的 `MainScreen.qml`、`CameraGrid.qml`、`CameraTile.qml`、`NGOverlay.qml` 和 `StatusBar.qml`，展示相机/视角图像、OK/NG/复检/异常计数、当前运行模式、状态原因和 NG 弹窗。
- 统计：展示前端持久化日志恢复后的 OK、NG、复检、异常、总数和缺陷分布。
- 日志：展示 Python detector 检测事件和 C++ 主控采集/超时/设备故障事件。
- 复核：操作员在 NG 弹窗中选择“标记待复核”后进入队列，确认/忽略动作会写入操作员事件日志。

前端会在 `trace_root` 下维护只读展示侧持久化文件：

- `display_operator_events.jsonl`：检测日志、主控告警和操作员动作追加日志。
- `display_review_queue.json`：待复核队列，前端重启后继续保留。

## 文件结构

```text
display_app/
├── main.py                         # PySide6/QML 前端入口
├── infrastructure/
│   └── image_provider.py           # image://camera 图像 provider
├── services/
│   ├── display_bridge.py           # 读取 display_latest.json 并更新图像 provider
│   ├── operator_journal.py         # 持久化操作员日志、动作和复核队列
│   └── image_loader.py             # 读取 trace PGM/PPM 图像为 numpy BGR
├── viewmodels/
│   └── main_viewmodel.py           # 兼容迁移 QML 所需属性/槽
├── qml/                            # 从 online-detection-app 迁移并收敛后的 QML 页面
└── resources/styles/               # 迁移的 QML 样式单例
```

## 数据来源

展示前端读取：

- `trace/display_latest.json`：最近一次 Python detector 输出，前端轮询。
- `trace/display_events.jsonl`：Python detector 检测事件追加日志。
- `trace/cpp_controller_events.jsonl`：C++ 主控采集、超时、设备故障和保守复检事件。
- `trace/display_operator_events.jsonl`：前端持久化的操作员日志和动作。
- `trace/display_review_queue.json`：前端持久化的复核队列。
- `trace/<date>/<seat>_<sequence>/raw_images/**/*.pgm`：原始采集图；模型资产未就绪或 ROI 未产出时用于直接展示。
- `trace/<date>/<seat>_<sequence>/images/**/*.pgm`：ROI 原图。
- `trace/<date>/<seat>_<sequence>/overlays/*.ppm`：缺陷叠加图。

展示桥会优先选择 ROI 图，缺少 ROI 图时回退到 raw 原始采集图；同一相机/视角下优先展示 `DIFFUSE`，再回退到其它光源。如果某次检测没有保存 trace 图像，前端仍会展示 OK/NG/RECHECK/ERROR、统计和日志；图像区域会等待下一次带图像的事件。

当模型资产未替换、ROI YOLO 缺失或 PatchCore/PCA 资产不可用时，Python detector 会返回 `RECHECK + CONFIGURATION_ERROR` 并在事件中标记 `sample_collection.enabled=true`。前端状态栏会显示“采样模式”，同时继续展示 raw 图，便于产线操作员确认拍摄效果并积累训练样本。
