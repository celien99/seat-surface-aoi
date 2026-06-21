# C++ 主控部署与硬件运维

本文整合原硬件对接、生产配置快速上手、生产上线 SOP、部署说明和测试机集成清单。当前仓库提供 C++ 主控框架、模拟驱动、Hikrobot MVS 相机 backend、FL-ACDH RS232 多控制器频闪 backend、TCP 信号 backend、生产配置校验、业务存储低水位治理和 fail-fast 保护；真实 PLC/外部信号网关、机器人和其它设备仍需要按现场 SDK 或协议接入。

## 边界

- C++ 负责 PLC 触发、相机采集、频闪控制、机器人 pose/shot 读取、共享内存写入、结果读取、PLC 输出和节拍控制。
- Python 只负责检测算法和共享内存结果写回，不能控制 PLC、相机或频闪。
- 在线图像和结果只走共享内存，不使用 TCP。
- 任意触发超时、缺帧、设备故障、协议错误、CRC 错误或 detector 超时都不能输出 `OK`，必须输出 `RECHECK` 或 `ERROR`。

## 当前控制链路

```text
PLC/工位触发
  -> C++ wait_trigger
  -> 根据 capture_mode 生成固定机位视角或机器人 pose 计划
  -> 等待视角/pose ready，机器人飞拍读取 SHOT_ID 和 TCP 位姿
  -> 按 capture_schedule 执行视角串行或共享光源并行采集
  -> 写入共享内存 frame slot
  -> Python detector 检测并写 result slot
  -> C++ 读取结果、做保守校验并输出 PLC OK/NG/RECHECK
```

C++ 主控通过 `capture_schedule` 配置采集调度。`view_serial_tdm` 会让当前视角完成全部光源后再切换下一视角；`shared_light_parallel` 仅用于固定机位共享光源场景，同一路光源频闪前先 arm 所有固定机位相机并同步收图。固定机位模式下检测视角通常等于 `camera_id`；机器人飞拍模式下检测视角等于 `pose_id`，必须保持 pose 级串行，不能配置共享光源并行。

当前固定机位接线以 FL-ACDH F 口同步输出触发相机 `Line0`，相机 `Line1` 的 `ExposureStartActive` 保留用于调试或后续 GPIO 同步方案。C++ 负责配置、arm、收图和故障判断；真实频闪时刻由频闪控制器、相机触发线或现场 IO/PLC/运动控制器完成，不能让 Python 参与触发时序。

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

# 机器人飞拍端到端模拟 IPC
uv run python tools/run_simulated_ipc.py --config cpp_controller/config/station_runtime.robot_flyshot.example.conf

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

机器人飞拍生产配置：

```powershell
cpp_controller/build/seat_aoi_controller `
  --config cpp_controller/config/station_runtime.robot_flyshot.production.conf `
  --validate-config
```

## 生产配置流程

先选择采集方案并复制模板：

```powershell
# 固定机位
cp cpp_controller/config/station_runtime.production.example.conf `
   cpp_controller/config/station_runtime.production.conf

# 机器人飞拍
cp cpp_controller/config/station_runtime.robot_flyshot.production.example.conf `
   cpp_controller/config/station_runtime.robot_flyshot.production.conf
```

不要直接修改 `*.production.example.conf`。

生产配置必须设置：

```ini
hardware_mode=production
signal.backend=tcp_signal
camera.backend=hikrobot_mvs
light.backend=serial_ascii
```

机器人飞拍还必须设置非模拟机器人后端：

```ini
capture_mode=robot_flyshot
robot.backend=vendor_sdk
```

常见 backend：

| 设备 | 可选值 |
| --- | --- |
| PLC | `modbus_tcp`、`siemens_s7`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` |
| 相机 | `hikrobot_mvs`、`basler_pylon`、`daheng_galaxy`、`flir_spinnaker`、`vendor_sdk`、`custom_sdk` |
| 频闪 | `serial_ascii`、`modbus_tcp`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` |
| 机器人 | `vendor_sdk`、`custom_sdk`、`modbus_tcp`、`digital_io` |

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


PLC 未接入前先使用实验室/工控机手动触发配置：

```powershell
cp cpp_controller/config/station_runtime.lab_manual.example.conf `
   cpp_controller/config/station_runtime.lab_manual.conf
# 先替换 TODO_CAMERA_SN_TOP_BACK 和 light.trigger_input_line 等现场接线参数。
cpp_controller/build/seat_aoi_controller `
  --config cpp_controller/config/station_runtime.lab_manual.conf `
  --validate-config
```

`hardware_mode=lab` 允许 `signal.backend=manual_trigger`，用于真实相机和真实频闪接入后的手动触发联调。该模式只生成测试触发并记录检测结果，不输出真实外部 IO，不能作为产线放行配置。正式生产配置仍必须使用真实外部信号 backend，且 `hardware_mode=production` 会拒绝 `manual_trigger` 和 `simulated` backend。

当前固定机位测试机硬件基线：

| 模块 | 已确认型号/参数 | C++ 配置影响 |
| --- | --- | --- |
| 相机 | 海康 MV-CH120-20GC，4096 x 3072 | `camera.backend=hikrobot_mvs`，`camera.0.width=4096`，`camera.0.height=3072`。 |
| 镜头 | MVL-KF0814M-12MPE FA 镜头，8mm F1.4，1.1"，C 接口 | 作为标定和视场参数记录，不作为当前运行配置字段解析。 |
| 频闪控制器 | FL-ACDH-20048-4，4 通道，当前使用通道 1/2/3；工位顶部 Dome 主光常亮且不受本程序控制 | `light.device_id=FL-ACDH-20048-4`，当前 `light_order=12,1,2,3` 且生产固定机位使用 `capture_schedule=shared_light_parallel`；`12` 为常亮 Dome ROI 采图，Python 固定机位生产配方已同步为 `DOME_ROI + 3` 个检测光源。 |
| PLC | 暂未定型 | 第一阶段可用手动/模拟触发测试相机、频闪、共享内存和 Python 收图；生产闭环前必须补齐 PLC 触发与输出点位。 |

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
light.serial_port=/dev/ttyUSB0
light.baud_rate=115200
light.response_mode=ack
light.trigger_input_line=TriggerIn1

capture_mode=fixed_camera
capture_schedule=shared_light_parallel
light_order=12,1,2,3
light.12.acquisition_mode=ambient
light.12.physical_channel=0
light.12.exposure_us=1200
light.12.strobe_width_us=0
light.12.trigger_delay_us=0
light.12.gain=1.0
light.12.current_percent=0
light.1.physical_channel=1
light.1.exposure_us=800
light.1.strobe_width_us=700
light.1.trigger_delay_us=10
light.1.gain=1.0
light.1.current_percent=60
```

`light.<N>.acquisition_mode` 默认为 `strobe`，会校验 `physical_channel/strobe_width_us/current_percent` 并触发频闪控制器；`ambient` 用于常亮 Dome ROI 采图，只校验曝光和增益，C++ 不会准备或触发频闪控制器，Hikrobot MVS 后端会改用 `TriggerSource=Software` 取图。

`light.response_mode` 默认为 `ack`，要求 FL-ACDH 每条串口命令返回 `$`。若现场控制器或接线确认无回包，可在联调配置中改为 `none`，程序只校验串口写入成功；后续仍必须通过相机取帧、控制器指示灯或示波器确认 `7` 命令触发成功。写入失败、取帧超时、协议错误或未确认状态仍必须输出 `RECHECK/ERROR`，不能放行 `OK`。

要求 `strobe_width_us <= exposure_us`，电流、脉宽和触发延时不得超过控制器与光源规格。

多控制器频闪使用控制器级 `light.<M>.<field>` 与通道级 `light.<M>.<N>.<field>`：

```ini
light.0.backend=serial_ascii
light.0.serial_port=COM3
light.0.baud_rate=115200
light.0.response_mode=ack
light.0.trigger_input_line=Line1
light.0.1.physical_channel=1

light.1.backend=serial_ascii
light.1.serial_port=COM4
light.1.baud_rate=115200
light.1.response_mode=ack
light.1.trigger_input_line=Line1
light.1.3.physical_channel=1
```

C++ 会按 `controller_index` 分别准备频闪序列并派发触发；生产校验会拒绝引用未配置控制器的 `light.<M>.<N>`。

### 存储治理

生产配置建议保留默认存储保护：

```ini
image_save.cleanup_enabled=true
image_save.cleanup_min_free_ratio=0.20
image_save.cleanup_trace_root=true
image_save.fail_on_save_error=true
```

每次检测前 C++ 会检查 `image_save.root_dir` 和 `trace_root` 所在磁盘水位；低于阈值时只清理 `YYYYMMDD` 日期目录下的历史业务文件。`display_latest.json`、前端操作日志、非日期目录和其它配置文件不会被扫描删除。清理后容量仍不足，或启用原图落盘时 PGM 写入失败，当前任务必须输出 `RECHECK/DeviceFault`，避免磁盘写满后继续运行。

### 机器人飞拍

机器人飞拍方案额外需要：

| 参数 | 用途 |
| --- | --- |
| READY/FAULT/START 信号 | C++/PLC 与机器人安全握手。 |
| pose 到位或过点信号 | 匹配 `pose.<N>.pose_id`。 |
| SHOT_ID 和 PHOTO_TRIGGER | 防止触发错序并追溯拍照位置。 |
| TCP 位姿和标定版本 | 写入共享内存，Python 用于 ROI/标定选择。 |

配置示例：

```ini
robot.backend=vendor_sdk
robot.controller_id=FANUC_M20ID25_AOI
robot.host=192.168.1.30
robot.ready_input=ROBOT_READY
robot.fault_input=ROBOT_FAULT
robot.start_output=AOI_START

pose.0.pose_id=T1_BACKREST
pose.0.camera_index=0
pose.0.camera_id=EYE_IN_HAND
pose.0.calibration_id=calib/t1_robot_v1
pose.0.shot_id_source=SHOT_ID_T1
pose.0.robot_ready_input=READY_T1
pose.0.robot_fault_input=ROBOT_FAULT
pose.0.photo_trigger_input=PHOTO_TRIGGER_T1
pose.0.robot_tcp_xyz_mm=350.0,120.0,220.0
pose.0.robot_rpy_deg=180.0,0.0,90.0
```

机器人未到位、FAULT、SHOT_ID 异常或位置触发超时必须返回 `RECHECK` 或 `ERROR`。

## 共享内存容量

一个座椅任务写入一个 frame slot，包含所有检测视角和所有光源：

```text
frame_count = view_count * light_order.size()
slot_size >= header + frame_count * sizeof(LightFrameMeta) + 所有图像字节数
```

示例：

```text
1 个固定机位 * 4 个光源 * 4096 * 3072 * Mono8 ~= 48 MB
2 个固定机位 * 4 个光源 * 4096 * 3072 * Mono8 ~= 96 MB
```

因此 MV-CH120-20GC 单机位模板使用 `frame_slot_size=67108864`；如果扩展到两个同分辨率固定机位，建议至少使用 `frame_slot_size=134217728`。默认 16 MB 只适合模拟小图，生产高分辨率图像必须使用同一份 C++ 运行配置启动 Python detector，或显式传入一致的 `--slot-count`、`--frame-slot-size` 和 `--result-slot-size`。

## 真实硬件接入位置

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 外部信号/PLC 边界 | `cpp_controller/include/control/isignal_client.hpp`、`cpp_controller/src/control/signal_client.cpp`、`cpp_controller/src/control/tcp_signal_client.cpp`、`cpp_controller/src/control/distance_trigger_signal_client.cpp` | 触发读取、SN/seat 元数据、结果输出和外部信号健康状态。 |
| 机器人 | `cpp_controller/include/control/irobot_client.hpp`、`cpp_controller/src/control/robot_client.cpp` | pose ready、SHOT_ID、位姿和健康状态。 |
| 相机 | `cpp_controller/src/camera/hikrobot_mvs_camera.cpp`、`cpp_controller/src/camera/camera_device.cpp` | 海康 MVS 初始化、arm、采图、图像元数据；模拟相机生成合成图像。 |
| 频闪 | `cpp_controller/src/control/light_controller.cpp` | 通道参数、arm、触发状态、关闭输出。 |
| backend 工厂 | `cpp_controller/include/control/hardware_factory.hpp` | 按配置创建真实或模拟后端。 |
| 采集编排 | `cpp_controller/src/control/frame_assembler.cpp` | 光源轮次、当前视角等待、结构化失败处理。 |
| 主流程 | `cpp_controller/src/control/station_controller.cpp` | 发布共享内存、等 Python 结果、PLC 输出。 |

真实 SDK 接入时不要把深度学习推理放进 C++。

## 测试机联调顺序

1. 只接相机，保留模拟 PLC 和模拟频闪，验证图像尺寸、stride、像素格式、timestamp 和 Python 收图。
2. 使用 `station_runtime.lab_manual.example.conf` 切到 `hardware_mode=lab` 和 `signal.backend=manual_trigger`，接真实相机/真实频闪，低频手动触发验证 `light_index -> physical_channel` 映射和 `shutdown_all()`。
3. 按当前 FL-ACDH F 口同步输出到相机 `Line0` 的接线，用示波器或控制器日志确认曝光窗口内点亮；联调脚本会把 C++ 配置传给 Python detector，同步 128 MB frame slot。
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
uv run python tools/run_simulated_ipc.py --config cpp_controller/config/station_runtime.robot_flyshot.example.conf
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
| 机器人未到位、FAULT、SHOT_ID 异常 | C++ 输出 `RECHECK` 或 `ERROR`。 |
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
  exposure_output_line: Line1 (ExposureStartActive -> strobe trigger input)
  trigger_input_line: Line0 (reserved; current backend uses MVS TriggerSoftware)
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
