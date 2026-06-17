# C++ 主控部署与硬件运维

本文整合原硬件对接、生产配置快速上手、生产上线 SOP、部署说明和测试机集成清单。当前仓库只提供 C++ 主控框架、模拟驱动、生产配置校验和 fail-fast 保护；真实 PLC、机器人、相机和频闪控制器需要按现场 SDK 或协议接入。

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
  -> 等待当前视角/pose ready，机器人飞拍读取 SHOT_ID 和 TCP 位姿
  -> 按 light_order 逐光源串行采集
  -> 写入共享内存 frame slot
  -> Python detector 检测并写 result slot
  -> C++ 读取结果、做保守校验并输出 PLC OK/NG/RECHECK
```

C++ 主控固定采用视角级串行 TDM 采集：当前视角完成全部光源后再切换下一视角。固定机位模式下检测视角通常等于 `camera_id`；机器人飞拍模式下检测视角等于 `pose_id`。不要实现多视角并行频闪采集，否则容易造成光源互相污染。

推荐同步模式：

```ini
trigger_sync_mode=camera_exposure_output
```

该模式表达的生产意图是 C++ 负责配置、arm、收图和故障判断，真实频闪时刻由相机曝光输出、IO 脉冲或 PLC/运动控制器硬触发完成。`software` 模式只用于模拟测试或低精度联调。

## 常用运行命令

```bash
cmake -S cpp_controller -B cpp_controller/build
cmake --build cpp_controller/build

# 一次模拟任务
cpp_controller/build/seat_aoi_controller --once --wait-ms 8000

# 连续模拟任务
cpp_controller/build/seat_aoi_controller --loop --max-jobs 3 --wait-ms 8000

# 固定机位端到端模拟 IPC
bash tools/run_simulated_ipc.sh

# 机器人飞拍端到端模拟 IPC
bash tools/run_simulated_ipc.sh --config cpp_controller/config/station_runtime.robot_flyshot.example.conf

# 短时长稳压测
bash tools/run_cpp_soak.sh --jobs 20 --wait-ms 8000
```

生产配置只校验字段，不启动硬件：

```bash
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.production.conf \
  --validate-config
```

机器人飞拍生产配置：

```bash
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.robot_flyshot.production.conf \
  --validate-config
```

## 生产配置流程

先选择采集方案并复制模板：

```bash
# 固定机位
cp cpp_controller/config/station_runtime.production.example.conf \
   cpp_controller/config/station_runtime.production.conf

# 机器人飞拍
cp cpp_controller/config/station_runtime.robot_flyshot.production.example.conf \
   cpp_controller/config/station_runtime.robot_flyshot.production.conf
```

不要直接修改 `*.production.example.conf`。

生产配置必须设置：

```ini
hardware_mode=production
plc.backend=modbus_tcp
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

当前固定机位测试机硬件基线：

| 模块 | 已确认型号/参数 | C++ 配置影响 |
| --- | --- | --- |
| 相机 | 海康 MV-CH120-20GC，4096 x 3072 | `camera.backend=hikrobot_mvs`，`camera.0.width=4096`，`camera.0.height=3072`。 |
| 镜头 | MVL-KF0814M-12MPE FA 镜头，8mm F1.4，1.1"，C 接口 | 作为标定和视场参数记录，不作为当前运行配置字段解析。 |
| 频闪控制器 | FL-ACDH-20048-4，4 通道 | `light.device_id=FL-ACDH-20048-4`，`light_order=1,2,3,4`，默认逻辑光源 1..4 映射物理通道 1..4。 |
| PLC | 暂未定型 | 第一阶段可用手动/模拟触发测试相机、频闪、共享内存和 Python 收图；生产闭环前必须补齐 PLC 触发与输出点位。 |

## 必填现场参数

### PLC

| 参数 | 用途 |
| --- | --- |
| PLC 品牌、通信方式、IP/端口或 IO 卡 | 决定 `PlcClient` 实现。 |
| 触发输入、触发边沿或握手方式 | 实现 `wait_trigger`，避免重复检测同一座椅。 |
| `trigger_id`、`seat_id`、`sku` 来源 | 写入共享内存并用于配方、日志和结果校验。 |
| OK/NG/RECHECK 输出点位、ack 输入、保持时间 | 实现 `send_decision`。 |
| 通信超时、重连和断线策略 | 任意不确定状态都不能输出 `OK`。 |

配置示例：

```ini
plc.host=192.168.1.10
plc.port=502
plc.station_id=LINE1_AOI_01
plc.trigger_source=DI0
plc.trigger_id_source=HR100
plc.seat_id_source=HR120
plc.sku_source=HR160
plc.ok_output=DO0
plc.ng_output=DO1
plc.recheck_output=DO2
plc.ack_input=DI1
plc.output_hold_ms=200
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
light.trigger_input_line=TriggerIn1

light_order=1,2,3,4
light.1.physical_channel=1
light.1.exposure_us=800
light.1.strobe_width_us=700
light.1.trigger_delay_us=10
light.1.gain=1.0
light.1.current_percent=60
```

要求 `strobe_width_us <= exposure_us`，电流、脉宽和触发延时不得超过控制器与光源规格。

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

因此 MV-CH120-20GC 单机位模板使用 `frame_slot_size=67108864`；如果扩展到两个同分辨率固定机位，建议至少使用 `frame_slot_size=134217728`。默认 16 MB 只适合模拟小图，生产高分辨率图像必须同时调整 C++ 和 Python 的共享内存 slot 配置。

## 真实硬件接入位置

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| PLC | `cpp_controller/src/control/plc_client.cpp` | 触发读取、结果输出、PLC 健康状态。 |
| 机器人 | `cpp_controller/include/control/irobot_client.hpp`、`cpp_controller/src/control/robot_client.cpp` | pose ready、SHOT_ID、位姿和健康状态。 |
| 相机 | `cpp_controller/src/camera/camera_device.cpp` | 初始化、arm、采图、图像元数据。 |
| 频闪 | `cpp_controller/src/control/light_controller.cpp` | 通道参数、arm、触发状态、关闭输出。 |
| backend 工厂 | `cpp_controller/include/control/hardware_factory.hpp` | 按配置创建真实或模拟后端。 |
| 采集编排 | `cpp_controller/src/control/frame_assembler.cpp` | 光源轮次、当前视角等待、结构化失败处理。 |
| 主流程 | `cpp_controller/src/control/station_controller.cpp` | 发布共享内存、等 Python 结果、PLC 输出。 |

真实 SDK 接入时不要把深度学习推理放进 C++。

## 测试机联调顺序

1. 只接相机，保留模拟 PLC 和模拟频闪，验证图像尺寸、stride、像素格式、timestamp 和 Python 收图。
2. 接频闪控制器，低频手动触发，验证 `light_index -> physical_channel` 映射和 `shutdown_all()`。
3. 开启 `camera_exposure_output` 硬触发链路，用示波器或控制器日志确认曝光窗口内点亮。
4. 接 PLC 触发输入，只读触发，不输出分拣信号，验证防重复触发、`seat_id`、`sku` 和 `trigger_id`。
5. 接 PLC OK/NG/RECHECK 输出，低速节拍人工确认输出点位和 ack/复位逻辑。
6. 连续运行 30 分钟、2 小时、8 小时，统计缺帧、光源故障、PLC 通信超时、共享内存 slot 泄漏和 detector timeout。

## 上线验收

配置验收：

```bash
uv run python -m tools.validate_protocol
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.production.conf \
  --validate-config
```

模拟链路验收：

```bash
bash tools/run_simulated_ipc.sh
bash tools/run_simulated_ipc.sh --config cpp_controller/config/station_runtime.robot_flyshot.example.conf
```

健康报警验收：

- C++ 事件日志写入 `trace_root/cpp_controller_events.jsonl`。
- 连续复检达到 `warning_recheck_threshold` 后进入 `Warning`。
- 连续复检达到 `critical_recheck_threshold` 后进入 `Fault/Critical`。
- PLC 输出失败、设备故障和 detector 超时能定位到 `sequence_id` 和 `trigger_id`。

长稳压测：

```bash
bash tools/run_cpp_soak.sh --jobs 1000 --wait-ms 8000 \
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

```bash
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
  exposure_output_line: Line1
  trigger_input_line: Line0
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

trigger_sync_mode:
frame_slot_size:
detector_timeout_ms:
camera_timeout_ms:
light_timeout_ms:
```

这些参数必须和 C++ 运行配置、Python 配方、标定文件和 ROI 模板保持一致。
