# PySide6/QML 展示前端

`display_app/` 是从 `/Users/yyh/code/online-detection-app` 迁移出的产线展示层，默认只负责读取当前项目 Python detector 与 C++ 主控生成的只读事件文件，显示线上检测、采样和复核状态。

它不会初始化相机、PLC、频闪、机器人或 `seat_defect_core`，也不会直接读写 C++/Python 在线共享内存。显式启用手动触发后，首页按钮只作为 C++ `tcp_signal` 的外部信号模拟源，按 `start_sn` 协议发送到位信号和 SN 条码；后续采集、频闪、共享内存和检测结果校验仍全部由 C++ 主控与 Python detector 完成。在线主链路仍然是：

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

`--trace-root` 必须指向 detector 输出 `display_latest.json` / `display_events.jsonl` 的目录。默认使用根目录 `trace/`。

手动触发入口收到 C++ `sn_ack` 后不会立即恢复按钮，而是继续保持加载态并等待 detector/C++ 写出同一 SN 的新版展示事件。默认等待 30 秒，现场节拍更长时可通过 `--manual-trigger-result-timeout-ms` 调整。

## 工控机交付方式

生产交付时，`display_app` 不注册为 Windows Service。它是需要桌面会话的 GUI 程序，由 `tools/windows/install_station.ps1` 创建桌面快捷方式启动；后台只注册 Python detector 和 C++ 主控两个服务。

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows\install_station.ps1 `
  -LineId LINE1_AOI_01 `
  -GridLayout 2x1
```

快捷方式默认创建到公共桌面，目标根据是否启用 `-BuildPythonPackages` 自动选择：

- **开发模式**（默认，不打包）：通过 `pythonw.exe` 直接运行源码，便于调试。
  ```powershell
  .\.venv\Scripts\pythonw.exe -m display_app.main --trace-root trace --line-id LINE1_AOI_01 --grid-layout 2x1
  ```

- **生产交付模式**（附加 `-BuildPythonPackages`）：快捷方式指向 PyInstaller 打包的独立 exe，不依赖 Python 环境。
  ```powershell
  bin\seat_aoi_display\seat_aoi_display.exe --trace-root <ProjectRoot盘符>:\seat-aoi-data\trace --line-id LINE1_AOI_01 --grid-layout 2x1
  ```
  默认 trace 根目录跟随 `ProjectRoot` 所在盘符；安装时显式传入 `-DataRoot` 会覆盖该路径。
  PyInstaller 使用 `--windowed --onedir` 构建，不弹出控制台窗口；打包后通过 `sys._MEIPASS` 解析 QML 和资源路径。

如只希望当前用户可见，安装时追加 `-CurrentUserShortcut`；如需要登录 Windows 后自动打开展示前端，追加 `-CreateStartupShortcut`。生产快捷方式默认不启用 `--enable-manual-trigger`；启用后应连接 C++ `display_manual_trigger.port` 独立手动端口，不能改连 PLC/上位机使用的 `signal.port`。

需要让桌面快捷方式支持手动触发全链路时，安装时显式追加：

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\windows\install_station.ps1 `
  -EnableDisplayManualTrigger `
  -ManualTriggerHost 127.0.0.1 `
  -ManualTriggerPort 9002 `
  -LineId LINE1_AOI_01 `
  -GridLayout 2x1
```

该快捷方式会把 `--enable-manual-trigger`、`--manual-trigger-host` 和 `--manual-trigger-port` 写入启动参数；按钮仍只发送控制面触发信号，不直接控制相机、频闪或共享内存。生产配置默认 `display_manual_trigger.port=9002`，外部自动触发仍使用 `signal.port=9000`。

联调时可显式启用首页手动触发按钮：

```powershell
uv run seat-aoi-display `
  --trace-root trace `
  --line-id AOI-1 `
  --enable-manual-trigger `
  --manual-trigger-host 127.0.0.1 `
  --manual-trigger-port 9002
```

手动触发客户端会向 C++ 独立手动端口发送 `start`，收到 `start_ack` 后发送 `sn <SN>`，收到 `sn_ack` 后界面进入"等待结果"加载态并保持 SN 输入框和按钮禁用，直到收到同一 SN、`SN_HHMMSSffffff` 或兼容旧站点前缀 seat_id 的新版 detector 展示事件后才恢复并自动清空 SN。等待结果阶段默认 30 秒超时，超时后解除禁用并显示触发异常，便于操作员确认链路状态后重新触发。SN 只允许字母、数字、横线、下划线和点，最大 48 个字符，避免写入共享内存 `seat_id` 时被截断。默认未加 `--enable-manual-trigger` 时按钮保持"只读展示"，不会连接 C++ 触发端口。

如果 C++ `tcp_signal` 正在监听但没有客户端连接，或客户端尚未提交完整 `start` + `sn <SN>`，这属于外部触发空闲等待；前端不会把它显示为复检，也不会增加复检统计。只有 C++ 已收到完整触发并进入采集/检测后返回的 `RECHECK/ERROR`，才会作为业务结果展示。

## 当前页面

- 监控：复用迁移的 `MainScreen.qml`、`CameraGrid.qml`、`CameraTile.qml`、`NGOverlay.qml` 和 `StatusBar.qml`，展示相机/视角图像、OK/NG/复检/异常计数、当前运行模式、状态原因和 NG 弹窗；启用手动触发时额外显示 SN 输入框和触发按钮。
- 统计：展示当前前端会话收到的 OK、NG、复检、异常、总数和缺陷分布；历史 `display_operator_events.jsonl` 保留在磁盘中，但不会在重启后污染当前班次统计。
- 日志：展示当前会话的 Python detector 检测事件和 C++ 主控告警事件；`station_ready`、`inspection_start` 等主控状态事件不会作为复检日志显示。
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
│   ├── display_bridge.py           # 读取 display_events.jsonl/display_latest.json 并更新图像 provider
│   ├── manual_trigger_client.py     # 可选 start_sn TCP 手动触发客户端
│   ├── operator_journal.py         # 持久化操作员日志、动作和复核队列
│   └── image_loader.py             # 读取 trace PNG，并兼容旧 PGM/PPM 图像为 numpy BGR
├── viewmodels/
│   └── main_viewmodel.py           # 兼容迁移 QML 所需属性/槽
├── qml/                            # 从 online-detection-app 迁移并收敛后的 QML 页面
└── resources/styles/               # 迁移的 QML 样式单例
```

## 数据来源

展示前端读取：

- `trace/display_events.jsonl`：Python detector 检测事件追加日志，前端统计、日志、NG 弹窗和手动触发完成判定优先以它为准，避免轮询间隔内多次检测被覆盖。
- `trace/display_latest.json`：最近一次 Python detector 输出，前端在没有新 JSONL 事件时用于启动恢复和兼容旧通道。
- `trace/cpp_controller_events.jsonl`：C++ 主控采集、超时、设备故障和保守复检事件。
- `trace/display_operator_events.jsonl`：前端持久化的操作员日志和动作。
- `trace/display_review_queue.json`：前端持久化的复核队列。
- `trace/<date>/<seat>_<sequence>/raw_images/**/*.png`：原始采集图；模型资产未就绪或 ROI 未产出时用于直接展示。
- `trace/<date>/<seat>_<sequence>/images/**/*.png`：ROI 原图。
- `trace/<date>/<seat>_<sequence>/overlays/<camera>/<pose>/<roi>.png`：检测叠加图；OK 件也会有绿色判定边框，NG/RECHECK/ERROR 有缺陷候选时额外显示候选框。

展示桥会优先选择 raw 原始采集图，缺少 raw 图时回退到 ROI 图；同一相机/视角下优先展示 `DIFFUSE`，再回退到其它光源。检测 overlay 也以 raw 原图尺寸输出，便于前端保持原始视野。如果某次检测没有保存 trace 图像，前端仍会展示 OK/NG/RECHECK/ERROR、统计和日志；图像区域会等待下一次带图像的事件。若事件中的图像路径存在但解码失败，前端会清空该相机/视角当前画面并显示“图像加载失败”，不会继续展示上一件旧图。

前端启动时如果 `display_latest.json` 仍是上一次运行留下的旧结果，只更新图像和状态，不计入当前会话统计、不追加新的操作员日志，也不会触发 NG 弹窗；收到当前会话的新 detector 事件后才开始计数。

当模型资产未替换、ROI YOLO 缺失或 PatchCore/PCA 资产不可用时，Python detector 会返回 `RECHECK + CONFIGURATION_ERROR` 并在事件中标记 `sample_collection.enabled=true`。前端状态栏会显示”采样模式”，同时继续展示 raw 图，便于产线操作员确认拍摄效果并积累训练样本。

当 Python detector 返回的 `RECHECK/ERROR` 消息匹配 ROI 未识别到目标物体模式（如”未识别到目标”、”目标丢失”、”ROI未匹配”等），前端将其展示为**信息性黄色提示**而非告警/复检红色错误。此类事件不会触发 `trigger_error` 告警，状态栏正常显示”在线检测”，决策统计仍按实际 decision 字段记录。

## 多机位 NG 展示规则

`display_app` 不再把一次检测事件压缩成单个最高分缺陷来驱动整屏状态。同一事件中只要某个机位存在 `decision=NG` 的缺陷，监控面板对应机位就会标记为 NG，并显示该机位最高分缺陷及数量。NG 弹窗仍以全局最高分缺陷作为预览图，但会同步显示全部 NG 机位和总缺陷数，避免双机位或多机位同时 NG 时只看到一个机位结果。

## 手动触发边界

- 手动触发只提交控制面信号，不传输图像，也不绕过 C++ 的采集、检测等待和结果保守校验。
- 手动触发提交中按钮显示"提交中"并禁用输入控件；收到 C++ 确认后切换为"等待结果"加载态，直到对应 SN 的检测展示事件刷新后才恢复并清空输入框，避免操作员重复点击或重复扫码。
- 生产配置中 `display_manual_trigger` 独立监听手动端口，默认 9002；PLC/外部工控机自动触发继续使用 `signal.port`，默认 9000。两个端口不能配置成同一个值。
- 完整触发进入采集/检测后，C++ 缺帧、设备故障、共享内存错误、Python detector 超时或质量门禁失败仍只能返回 `RECHECK` 或 `ERROR`，前端按钮不会改变判定规则。
