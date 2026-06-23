# C++ 主控部署与硬件运维

本文整合原硬件对接、生产配置快速上手、生产上线 SOP、部署说明和测试机集成清单。当前仓库提供固定双机位共享频闪 C++ 主控、模拟驱动、Hikrobot MVS 相机 backend、FL-ACDH RS232 频闪 backend、TCP 信号 backend、生产配置校验、业务存储低水位治理和 fail-fast 保护；真实 PLC/外部信号网关如果不使用当前 TCP 归一化协议，需要按 `ISignalClient` 接口补齐现场适配器。

## 边界

- C++ 负责外部触发、相机采集、频闪控制、共享内存写入、结果读取、外部结果输出和节拍控制。
- Python 只负责检测算法和共享内存结果写回，不能控制 PLC、相机或频闪。
- 在线图像和结果只走共享内存，不使用 TCP。
- 任意触发超时、缺帧、设备故障、协议错误、CRC 错误或 detector 超时都不能输出 `OK`，必须输出 `RECHECK` 或 `ERROR`。

## 当前控制链路

```text
PLC/工位触发或手动触发
  -> C++ wait_trigger
  -> 生成固定机位视角计划 camera.0/camera.1
  -> 按 light_order=1,2,3 执行共享光源并行采集
  -> 写入共享内存 frame slot
  -> Python detector 检测并写 result slot
  -> C++ 读取结果、做保守校验并输出 OK/NG/RECHECK
```

C++ 主控当前固定 `capture_mode=fixed_camera` 与 `capture_schedule=shared_light_parallel`。同一路光源频闪前先 arm 所有固定机位相机并同步收图，物理采集顺序是光源优先、相机并行；发布到共享内存前会重排为机位优先、光源顺序，便于 Python 按 `camera_id/view_id` 组包。

当前固定机位接线以 FL-ACDH 同步输出触发相机 `Line0`，现场已将输出接口 `F1~F3` 短接合成一根触发线，再并联到两台相机黄色 `Line0`。相机 `Line1` 的 `ExposureStartActive` 仅保留用于调试/示波器输出。C++ 负责配置、arm、收图和故障判断；真实频闪时刻由频闪控制器、相机触发线或现场 IO/PLC/运动控制器完成，不能让 Python 参与触发时序。

## 常用运行命令

```powershell
cmake -S cpp_controller -B cpp_controller/build
cmake --build cpp_controller/build

# 一次模拟任务
cpp_controller/build/seat_aoi_controller --once --wait-ms 8000

# 连续模拟任务
cpp_controller/build/seat_aoi_controller --loop --max-jobs 3 --wait-ms 8000

# 固定机位端到端模拟 IPC
uv run python tools/run_simulated_ipc.py

# 短时长稳压测
uv run python -m tools.run_cpp_soak --jobs 20 --wait-ms 8000

# 上 Windows 工控机前交接预检
uv run python -m tools.validate_deployment_preflight
uv run python -m tools.validate_deployment_preflight --strict-production
```

生产配置只校验字段，不启动硬件：

```powershell
cpp_controller/build/seat_aoi_controller `
  --config cpp_controller/config/station_runtime.production.conf `
  --validate-config
```

`tools.validate_deployment_preflight` 是源码树和部署包共用的上机前检查入口。默认模式用于交接，会确认本地参考链路、Windows Named Shared Memory 映射、跨平台模拟 IPC、部署包校验入口和 `lab/manual_trigger` 联调路径已经具备；真实模型资产和 MES/报警/监控协议会作为现场 ACTION 输出。`--strict-production` 用于工控机放行前，固定双机位正式生产配置缺失、光源/配方不一致或真实模型资产缺失时返回阻塞。

## 生产配置流程

当前仓库已直接维护 `cpp_controller/config/station_runtime.production.conf`、`station_runtime.test.conf` 和 `station_runtime.capture_only.conf`。现场修改优先在这三份运行配置上完成，并同步记录到本文和 [C++ 主控当前逻辑梳理](cpp_controller_current_logic.md)。

生产配置必须设置：

```ini
hardware_mode=production
signal.backend=tcp_signal
camera.backend=hikrobot_mvs
light.backend=serial_ascii
```

常见 backend：

| 设备 | 可选值 |
| --- | --- |
| 外部信号 | `simulated`、`manual_trigger`、`external_signal`、`tcp_signal` |
| 相机 | `simulated`、`hikrobot_mvs` |
| 频闪 | `simulated`、`serial_ascii` |

如果真实 SDK 尚未链接，非 `simulated` backend 会 fail-fast，程序不会回退到模拟硬件。

海康 MV-CH120-20GC 已有 MVS SDK 适配层。Windows 工控机安装 MVS 后，使用下列方式启用真实相机 backend：

```powershell
cmake -S cpp_controller -B cpp_controller/build `
  -DCMAKE_BUILD_TYPE=Release `
  -DSEAT_AOI_ENABLE_HIKROBOT_MVS=ON `
  -DSEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR="C:/Program Files (x86)/MVS/Development/Includes" `
  -DSEAT_AOI_HIKROBOT_MVS_LIBRARY="C:/Program Files (x86)/MVS/Development/Libraries/win64/MvCameraControl.lib"
cmake --build cpp_controller/build --config Release
```


PLC 或触发端未接入前先使用工控机手动触发配置：

```powershell
cpp_controller/build/seat_aoi_controller `
  --config cpp_controller/config/station_runtime.test.conf `
  --validate-config
```

`hardware_mode=lab` 允许 `signal.backend=manual_trigger`，用于真实相机和真实频闪接入后的手动触发联调。该模式只生成测试触发并记录检测结果，不输出真实外部 IO，不能作为产线放行配置。正式生产配置仍必须使用真实外部信号 backend，且 `hardware_mode=production` 会拒绝 `manual_trigger` 和 `simulated` backend。

当前固定机位测试机硬件基线：

| 模块 | 已确认型号/参数 | C++ 配置影响 |
| --- | --- | --- |
| 相机 | 海康 MV-CH120-20GC，4096 x 3072，SN `DA9184656` / `DA9184665` | `camera.backend=hikrobot_mvs`，`camera.0/1.width=4096`，`camera.0/1.height=3072`。 |
| 镜头 | MVL-KF0814M-12MPE FA 镜头，8mm F1.4，1.1"，C 接口 | 作为标定和视场参数记录，不作为当前运行配置字段解析。 |
| 频闪控制器 | FL-ACDH-20048-4，4 通道，当前使用通道 1/2/3 | `light.device_id=FL-ACDH-20048-4`，当前 `light_order=1,2,3` 且生产固定机位使用 `capture_schedule=shared_light_parallel`。 |
| 外部信号 | 生产使用 TCP 归一化触发/结果回传，测试使用手动触发 | 生产 `signal.backend=tcp_signal`；联调 `signal.backend=manual_trigger`。 |

## 必填现场参数

### PLC

| 参数 | 用途 |
| --- | --- |
| PLC 品牌、通信方式、IP/端口或 IO 卡 | 决定 `ISignalClient` backend 或现场协议适配器实现。 |
| 触发输入、触发边沿或握手方式 | 实现 `wait_trigger`，避免重复检测同一座椅。 |
| `trigger_id`、`seat_id`、`sku` 来源 | 写入共享内存并用于配方、日志和结果校验。 |
| OK/NG/RECHECK 输出点位、ack 输入、保持时间 | 实现 `send_decision`。 |
| 通信超时、重连和断线策略 | 任意不确定状态都不能输出 `OK`。 |

配置示例（当前实现用 `signal.*` 表达外部触发/结果回传；若现场使用 Modbus/S7/IO，需要按 `ISignalClient` 接口补齐对应 backend）：

```ini
signal.backend=tcp_signal
signal.station_id=LINE1_AOI_01
signal.default_sku=seat_a_black_leather
signal.port=9000
signal.delimiter=
signal.terminator=\n
signal.ok_response=ok\n
signal.result_host=192.168.1.100
signal.result_port=9001
signal.result_prefix=result
signal.result_delimiter=|
signal.ok_text=OK
signal.ng_text=NG
signal.recheck_text=RECHECK
signal.error_text=ERROR
```

### 相机

| 参数 | 用途 |
| --- | --- |
| 厂商、型号、SDK 版本 | 决定 `CameraDevice` 实现和链接库。 |
| 相机序列号到 `camera_index` 映射 | C++ 写入共享内存，Python 按 `camera_id/pose_id` 组包。 |
| 机位 ID、分辨率、像素格式、bit depth | 写入 `LightFrameMeta`，影响 slot 大小。 |
| 触发输入、曝光输出、极性和延时 | 用于硬触发同步频闪。 |
| 曝光、增益、buffer 数量、丢帧计数 | 用于采集质量和健康检查。 |

配置示例：

```ini
camera.0.camera_id=TOP_BACK
camera.0.serial_number=TODO_CAMERA_SN_TOP_BACK
camera.0.width=4096
camera.0.height=3072
camera.0.channels=1
camera.0.pixel_format=Mono8
camera.0.trigger_line=Line0
camera.0.exposure_output_line=Line1
camera.0.buffer_count=8
```

当前镜头为 MVL-KF0814M-12MPE，8mm F1.4，1.1"，C 接口。镜头型号、焦距和光圈不写入共享内存协议，但必须进入现场标定记录、ROI 版本和验收报告。

### 频闪

| 参数 | 用途 |
| --- | --- |
| 控制器品牌、型号、SDK 或协议 | 决定 `LightController` 实现。 |
| `light_index` 到物理通道映射 | 决定每轮点亮哪个真实光源。 |
| 触发输入线、输出模式、触发延时 | 对齐相机曝光窗口。 |
| 电流、脉宽、最小间隔和异常关闭命令 | 保证成像稳定和异常安全。 |

配置示例：

```ini
light.backend=serial_ascii
light.device_id=FL-ACDH-20048-4
light.serial_port=COM1
light.baud_rate=9600
light.response_mode=ack
light.trigger_input_line=F1

capture_mode=fixed_camera
capture_schedule=shared_light_parallel
light_order=1,2,3
light.1.physical_channel=1
light.1.exposure_us=30000
light.1.strobe_width_us=300
light.1.trigger_delay_us=10
light.1.gain=1.0
light.1.current_percent=60
```

`light.<N>.acquisition_mode` 当前只允许 `strobe`，会校验 `physical_channel/strobe_width_us/current_percent` 并触发频闪控制器。C++ 当前不支持 `ambient` 或软件触发采集常亮 Dome ROI 图。

`light.response_mode` 默认为 `ack`，要求 FL-ACDH 每条串口命令返回 `$`，并利用 ACK 读回节拍避免 `8/9/A/7` 连续过快写入导致触发命令早于控制器参数生效。当前串口远程触发链路不使用 IO/序列/组合联动，也不使用外部 Tx+/Tx- 输入边沿，因此不会在每次触发时发送现场控制器会拒绝的 `C/B` 命令。若现场控制器或接线确认无回包，可在联调配置中临时改为 `none`，程序只校验串口写入成功；后续仍必须通过相机取帧、控制器指示灯或示波器确认 `7` 命令触发成功。写入失败、取帧超时、协议错误或未确认状态仍必须输出 `RECHECK/ERROR`，不能放行 `OK`。

要求 `10 <= strobe_width_us <= 999`、`5 <= trigger_delay_us <= 99` 且 `strobe_width_us <= exposure_us`，电流、脉宽和触发延时不得超过控制器与光源规格。

当前生产校验只允许 1 台 FL-ACDH 控制器，`controller_index` 必须为 0。多控制器或其它光源协议属于后续扩展，不能直接用于当前生产配置。

### 存储治理

生产配置建议保留默认存储保护：

```ini
image_save.cleanup_enabled=true
image_save.cleanup_min_free_ratio=0.20
image_save.cleanup_trace_root=true
image_save.fail_on_save_error=true
```

每次检测前 C++ 会检查 `image_save.root_dir` 和 `trace_root` 所在磁盘水位；低于阈值时只清理 `YYYYMMDD` 日期目录下的历史业务文件。`display_latest.json`、前端操作日志、非日期目录和其它配置文件不会被扫描删除。清理后容量仍不足，或启用原图落盘时 PGM 写入失败，当前任务必须输出 `RECHECK/DeviceFault`，避免磁盘写满后继续运行。

## 共享内存容量

一个座椅任务写入一个 frame slot，包含所有检测视角和所有光源：

```text
frame_count = view_count * light_order.size()
slot_size >= header + frame_count * sizeof(LightFrameMeta) + 所有图像字节数
```

示例：

```text
1 个固定机位 * 3 个光源 * 4096 * 3072 * Mono8 ~= 36 MB
2 个固定机位 * 3 个光源 * 4096 * 3072 * Mono8 ~= 72 MB
```

因此当前双机位生产配置使用 `frame_slot_size=134217728`，为 6 帧 Mono8 高分辨率图像留出安全余量。默认 16 MB 只适合模拟小图，生产高分辨率图像必须使用同一份 C++ 运行配置启动 Python detector，或显式传入一致的 `--slot-count`、`--frame-slot-size` 和 `--result-slot-size`。

## 真实硬件接入位置

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 外部信号/PLC 边界 | `cpp_controller/include/control/isignal_client.hpp`、`cpp_controller/src/control/signal_client.cpp`、`cpp_controller/src/control/tcp_signal_client.cpp` | 触发读取、SN/seat 元数据、结果输出和外部信号健康状态。 |
| 相机 | `cpp_controller/src/camera/hikrobot_mvs_camera.cpp`、`cpp_controller/src/camera/camera_device.cpp` | 海康 MVS 初始化、arm、采图、图像元数据；模拟相机生成合成图像。 |
| 频闪 | `cpp_controller/src/control/fl_acdh_light_controller.cpp`、`cpp_controller/src/control/light_controller.cpp` | FL-ACDH 串口命令、通道参数、触发状态、关闭输出。 |
| backend 工厂 | `cpp_controller/include/control/hardware_factory.hpp` | 按配置创建真实或模拟后端。 |
| 采集编排 | `cpp_controller/src/control/frame_assembler.cpp` | 光源轮次、当前视角等待、结构化失败处理。 |
| 主流程 | `cpp_controller/src/control/station_controller.cpp` | 发布共享内存、等 Python 结果、PLC 输出。 |

真实 SDK 接入时不要把深度学习推理放进 C++。

## 测试机联调顺序

1. 只接相机，保留模拟 PLC 和模拟频闪，验证图像尺寸、stride、像素格式、timestamp 和 Python 收图。
2. 使用 `station_runtime.test.conf` 的 `hardware_mode=lab` 和 `signal.backend=manual_trigger`，接真实相机/真实频闪，低频手动触发验证 `light_index -> physical_channel` 映射和 `shutdown_all()`。
3. 按当前 FL-ACDH `F1` 同步输出总线到两台相机黄色 `Line0` 的接线，用示波器或控制器日志确认曝光窗口内点亮；联调脚本会把 C++ 配置传给 Python detector，同步 128 MB frame slot。
4. 接 PLC 触发输入，只读触发，不输出分拣信号，验证防重复触发、`seat_id`、`sku` 和 `trigger_id`。
5. 接 PLC OK/NG/RECHECK 输出，低速节拍人工确认输出点位和 ack/复位逻辑。
6. 连续运行 30 分钟、2 小时、8 小时，统计缺帧、光源故障、PLC 通信超时、共享内存 slot 泄漏和 detector timeout。

## 上线验收

配置验收：

```powershell
uv run python -m tools.validate_protocol
cpp_controller/build/seat_aoi_controller `
  --config cpp_controller/config/station_runtime.production.conf `
  --validate-config
```

模拟链路验收：

```powershell
uv run python tools/run_simulated_ipc.py
```

健康报警验收：

- C++ 事件日志写入 `trace_root/cpp_controller_events.jsonl`。
- 连续复检达到 `warning_recheck_threshold` 后进入 `Warning`。
- 连续复检达到 `critical_recheck_threshold` 后进入 `Fault/Critical`。
- PLC 输出失败、设备故障和 detector 超时能定位到 `sequence_id` 和 `trigger_id`。

长稳压测：

```powershell
uv run python -m tools.run_cpp_soak --jobs 1000 --wait-ms 8000 `
  --trace-root trace/cpp_soak_8h
```

放行条件：

- 配置验收通过。
- 模拟链路验收通过。
- 健康报警验收通过。
- 真实驱动验收通过。
- 8h/24h 长稳压测通过。
- Python detector 真实模型、配方、标定和 trace 链路同步验收通过。

## 必须验证的失败场景

| 场景 | 期望行为 |
| --- | --- |
| Python detector 不启动 | C++ 等待结果超时，输出 `RECHECK`。 |
| 任一相机缺帧或 arm 失败 | C++ 输出 `RECHECK`，错误码为 `MissingFrame` 或 `CameraFault`。 |
| 频闪配置、arm 或状态回读失败 | C++ 输出 `RECHECK` 或 `ERROR`。 |
| 曝光输出或硬触发确认失败 | C++ 输出 `RECHECK`，错误码为 `TriggerSyncFault`。 |
| PLC 触发协议错误或输出失败 | 不把本次结果当作 OK 完成。 |
| 共享内存 frame slot 满 | C++ 输出 `RECHECK`。 |
| 结果 CRC 或 payload 错误 | C++ 输出 `RECHECK` 或 `ERROR`。 |
| Python 返回 OK 但质量门禁失败 | C++ 降级 `RECHECK`。 |

已有模拟验证命令：

```powershell
cpp_controller/build/seat_aoi_controller --simulate-light-fault --wait-ms 200
cpp_controller/build/seat_aoi_controller --simulate-missing-frame --wait-ms 200
cpp_controller/build/seat_aoi_controller --simulate-trigger-timeout --trigger-timeout-ms 50
cpp_controller/build/ipc_safety_checks
```

## 现场参数记录模板

```text
工位名称：
PLC 型号：
PLC 通信方式：
触发输入点位：
OK 输出点位：
NG 输出点位：
RECHECK 输出点位：

camera_index=0:
  camera_id: TOP_BACK
  vendor/model: Hikrobot MV-CH120-20GC
  serial:
  lens: MVL-KF0814M-12MPE, 8mm F1.4, 1.1", C mount
  trigger_input_line: Line0 (from FL-ACDH F1 sync output bus)
  exposure_output_line: Line1 (ExposureStartActive debug output only)
  width: 4096
  height: 3072
  pixel_format: Mono8

light_index=1:
  light_name:
  controller_model: FL-ACDH-20048-4
  controller_channel:
  trigger_input:
  current_percent:
  exposure_us:
  strobe_width_us:
  trigger_delay_us:

frame_slot_size:
detector_timeout_ms:
camera_timeout_ms:
light_timeout_ms:
```

这些参数必须和 C++ 运行配置、Python 配方、标定文件和 ROI 模板保持一致。
