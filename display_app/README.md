# PySide6/QML 展示前端

`display_app/` 是从 `/Users/yyh/code/online-detection-app` 迁移出的轻量展示层，只负责读取当前项目 Python detector 生成的展示通道并显示线上检测结果。

它不会初始化相机、PLC、频闪、机器人或 `seat_defect_core`，也不会直接读写 C++/Python 在线共享内存。在线主链路仍然是：

```text
C++ 主控 -> 共享内存 Frame Ring -> Python detector -> 共享内存 Result Ring -> C++ 主控
                                      |
                                      v
                         trace/display_latest.json
                         trace/display_events.jsonl
                                      |
                                      v
                             PySide6/QML 展示前端
```

## 启动

```bash
uv sync --extra display
uv run seat-aoi-display --trace-root trace --line-id AOI-1
```

开发时也可以直接运行：

```bash
uv run python -m display_app.main --trace-root trace --poll-ms 300
```

`--trace-root` 必须指向 detector 输出 `display_latest.json` 的目录。默认使用根目录 `trace/`。

## 当前页面

- 监控：复用迁移的 `MainScreen.qml`、`CameraGrid.qml`、`CameraTile.qml`、`NGOverlay.qml` 和 `StatusBar.qml`，展示相机/视角图像、OK/NG 计数、最近结果和 NG 弹窗。
- 统计：展示当前前端会话内累计 OK、NG、总数和缺陷分布。
- 日志：展示当前前端会话内收到的检测事件。
- 复核：操作员在 NG 弹窗中选择“标记待复核”后进入队列。

统计、日志和复核目前是前端会话内状态；长期持久化可以后续基于 `display_events.jsonl` 或 SQLite 扩展。

## 文件结构

```text
display_app/
├── main.py                         # PySide6/QML 前端入口
├── infrastructure/
│   └── image_provider.py           # image://camera 图像 provider
├── services/
│   ├── display_bridge.py           # 读取 display_latest.json 并更新图像 provider
│   └── image_loader.py             # 读取 trace PGM/PPM 图像为 numpy BGR
├── viewmodels/
│   └── main_viewmodel.py           # 兼容迁移 QML 所需属性/槽
├── qml/                            # 从 online-detection-app 迁移并收敛后的 QML 页面
└── resources/styles/               # 迁移的 QML 样式单例
```

## 数据来源

展示前端读取：

- `trace/display_latest.json`：最近一次 Python detector 输出，前端轮询。
- `trace/display_events.jsonl`：检测事件追加日志，当前版本保留为后续持久化/回放扩展。
- `trace/<date>/<seat>_<sequence>/raw_images/**/*.pgm`：原始采集图；模型资产未就绪或 ROI 未产出时用于直接展示。
- `trace/<date>/<seat>_<sequence>/images/**/*.pgm`：ROI 原图。
- `trace/<date>/<seat>_<sequence>/overlays/*.ppm`：缺陷叠加图。

展示桥会优先选择 ROI 图，缺少 ROI 图时回退到 raw 原始采集图；同一相机/视角下优先展示 `DIFFUSE`，再回退到其它光源。如果某次检测没有保存 trace 图像，前端仍会展示 OK/NG/RECHECK/ERROR、统计和日志；图像区域会等待下一次带图像的事件。
