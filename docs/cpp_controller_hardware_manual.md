# C++ 主控硬件集成与使用手册

本文说明后续如何把当前 C++ 模拟主控接入真实 PLC、机器人、相机和频闪控制器。当前仓库已经实现共享内存 IPC、模拟 PLC 触发、固定机位/机器人飞拍多光源采集编排、`camera_exposure_output` 硬触发同步模拟链路；真实硬件接入时只替换 C++ 设备驱动层，不修改 Python 检测进程控制边界，也不改在线 IPC 为 TCP。

## 1. 当前控制链路

默认运行模式：

```text
PLC/工位触发
  -> C++ wait_trigger
  -> C++ 根据 capture_mode 生成固定机位视角或机器人 pose 计划
  -> C++ 等待当前视角/pose ready，机器人飞拍读取 SHOT_ID 和 TCP 位姿
  -> C++ 根据 light_order 逐个光源轮次执行
  -> C++ 配置频闪通道参数
  -> C++ arm 当前频闪通道
  -> C++ arm 当前机位相机
  -> 相机曝光输出或外部硬触发触发频闪
  -> C++ 等待当前机位图像
  -> C++ 将同一座椅任务的所有图像写入共享内存
  -> Python detector 读取共享内存并检测
  -> C++ 读取结果并输出 PLC OK/NG/RECHECK
```

当前默认同步模式是：

```ini
trigger_sync_mode=camera_exposure_output
```

该模式表达的生产意图是：C++ 不依赖软件命令精确点亮频闪，而是负责配置、arm、收图和故障判断；频闪真实点亮时机由硬件链路触发，推荐使用相机曝光输出、IO 脉冲或 PLC/运动控制器硬触发。

保留测试模式：

```ini
trigger_sync_mode=software
```

`software` 模式下 C++ 直接调用软件触发频闪，仅用于模拟测试或低精度联调，不建议作为高速生产线最终同步方案。

## 2. 程序运行方式

构建：

```bash
cmake -S cpp_controller -B cpp_controller/build
cmake --build cpp_controller/build
```

启动一次模拟任务：

```bash
cpp_controller/build/seat_aoi_controller --once --wait-ms 8000
```

连续模拟 3 件：

```bash
cpp_controller/build/seat_aoi_controller --loop --max-jobs 3 --wait-ms 8000
```

使用配置文件：

```bash
cpp_controller/build/seat_aoi_controller --config cpp_controller/config/station_runtime.example.conf
```

只校验生产配置，不启动共享内存、PLC、相机或频闪：

```bash
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.production.conf \
  --validate-config
```

端到端模拟 IPC：

```bash
bash tools/run_simulated_ipc.sh
```

运行前建议先启动或确认 Python detector 的运行策略。`tools/run_simulated_ipc.sh` 会自动启动一次 Python 检测进程；生产部署中 Python detector 应常驻运行，C++ 主控负责持续发布共享内存任务并等待结果。

## 3. 运行配置说明

当前模拟示例配置位于 `cpp_controller/config/station_runtime.example.conf`。生产配置模板位于 `cpp_controller/config/station_runtime.production.example.conf`，建议复制为 `station_runtime.production.conf` 后填写现场参数。

关键字段：

| 字段 | 说明 |
| --- | --- |
| `hardware_mode` | `simulated` 表示模拟硬件；`production` 表示生产硬件配置，必须填写真实 PLC、相机和频闪参数。 |
| `capture_mode` | `fixed_camera` 表示固定机位多光源；`robot_flyshot` 表示机器人飞拍多光源。 |
| `plc.backend` | PLC 后端，例如 `modbus_tcp`、`siemens_s7`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk`。 |
| `robot.backend` | 机器人或机器人 IO 网关后端，仅机器人飞拍生产方案必须配置为非 simulated。 |
| `camera.backend` | 相机后端，例如 `hikrobot_mvs`、`basler_pylon`、`daheng_galaxy`、`flir_spinnaker`、`vendor_sdk`、`custom_sdk`。 |
| `light.backend` | 频闪控制器后端，例如 `serial_ascii`、`modbus_tcp`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk`。 |
| `reset_shared_memory` | 启动时是否重置共享内存。生产中主控首次启动通常为 `true`，热重启策略需结合 detector 状态确认。 |
| `slot_count` | 共享内存环形缓冲槽位数。 |
| `frame_slot_size` | 每个图像帧 slot 的字节数；真实分辨率提高后必须增大。 |
| `result_slot_size` | 每个结果 slot 的字节数。 |
| `publish_timeout_ms` | C++ 等待可用 frame slot 的超时。超时输出 `RECHECK`。 |
| `detector_timeout_ms` | C++ 等待 Python 检测结果的超时。超时输出 `RECHECK`。 |
| `trigger_timeout_ms` | C++ 等待 PLC/外部触发的超时。超时不能输出 `OK`。 |
| `camera_timeout_ms` | 每个光源轮次下等待相机图像的超时。任一相机缺帧输出 `RECHECK`。 |
| `light_timeout_ms` | 配置、arm、确认频闪状态的超时。失败输出 `RECHECK`。 |
| `warning_recheck_threshold` | 连续复检达到该次数后进入 `Warning`。 |
| `critical_recheck_threshold` | 连续复检达到该次数后进入 `Fault/Critical`，必须大于 warning 阈值。 |
| `max_jobs` | 模拟运行批次数。`0` 表示 loop 模式无限运行。 |
| `recipe_id` | 写入共享内存任务的配方 ID，Python detector 用它加载配方。 |
| `light_order` | 光源轮次顺序，例如 `1,2,3,4`。它决定每个座椅任务采图顺序。 |
| `light.<N>.physical_channel` | 逻辑光源 `N` 对应的真实频闪控制器物理通道。 |
| `light.<N>.exposure_us` | 逻辑光源 `N` 的相机曝光时间，写入图像元数据。 |
| `light.<N>.strobe_width_us` | 逻辑光源 `N` 的频闪脉宽。 |
| `light.<N>.trigger_delay_us` | 逻辑光源 `N` 的频闪触发延时。 |
| `light.<N>.gain` | 逻辑光源 `N` 的相机增益。 |
| `light.<N>.current_percent` | 逻辑光源 `N` 的频闪电流或亮度百分比。 |
| `trigger_sync_mode` | 同步模式。推荐 `camera_exposure_output`；测试可用 `software`。 |
| `camera.<N>.serial_number` | 第 N 个机位的相机序列号，必须来自厂商工具。 |
| `camera.<N>.trigger_line` | 相机触发输入线。 |
| `camera.<N>.exposure_output_line` | 相机曝光输出或 StrobeOut 线，通常接频闪 TriggerIn。 |
| `plc.trigger_source` | PLC 触发输入点位或寄存器。 |
| `plc.ok_output/ng_output/recheck_output` | PLC 结果输出点位。 |
| `plc.ack_input` | PLC 已读取 C++ 输出的确认输入。 |
| `light.serial_port/light.host/light.device_id` | 频闪控制器的串口、网口或设备 ID，按 backend 类型填写。 |
| `light.trigger_input_line` | 频闪触发输入线。 |
| `pose.<N>.pose_id` | 机器人飞拍检测视角 ID，必须与 Python 配方中的 `pose_id` 对齐。 |
| `pose.<N>.shot_id_source` | 当前 pose 的 SHOT_ID 来源。 |
| `pose.<N>.photo_trigger_input` | 当前 pose 的位置触发/拍照触发输入。 |
| `pose.<N>.robot_tcp_xyz_mm` / `pose.<N>.robot_rpy_deg` | 当前 pose 的规划 TCP 位姿，用于共享内存追溯。 |
| `trace_root` | C++ 生产事件日志目录，默认写入 `trace/cpp_controller_events.jsonl`。 |
| `simulate_light_fault` | 模拟光源故障。 |
| `simulate_missing_frame` | 模拟相机缺帧。 |
| `simulate_plc_output_fault` | 模拟 PLC 输出失败。 |
| `simulate_trigger_timeout` | 模拟 PLC 触发超时。 |

生产配置校验规则：

- `hardware_mode=production` 时，`plc.backend`、`camera.backend`、`light.backend` 不能是 `simulated`。
- C++ 主控固定采用视角级串行 TDM 采集路径，不支持外部配置采集策略；生产模式必须使用 `camera_exposure_output` 或等价硬触发同步。
- `strobe_width_us <= exposure_us`，且 `frame_slot_size` 必须足够容纳完整串行 TDM 图像包。
- 生产必填字段不能留空，也不能保留 `TODO` 占位。
- 配置校验通过只表示字段齐全，不表示真实驱动已经链接成功。
- 如果未接入对应 SDK 就直接运行生产 backend，程序会 fail-fast 报“尚未链接真实硬件驱动”，不会回退到模拟硬件。

更详细的逐项填写说明见 [C++ 主控生产配置快速上手](cpp_controller_production_config_quickstart.md)。

## 4. 真实硬件需要提供的参数

### 4.1 PLC/外部触发

必须提供：

| 参数 | 示例 | 用途 |
| --- | --- | --- |
| PLC 品牌和通信方式 | Siemens S7、Modbus TCP、EtherCAT、IO 卡 | 决定 `PlcClient` 驱动实现。 |
| 触发输入点位或寄存器 | `I0.0`、`coil 100`、`DI3` | 实现 `wait_trigger`。 |
| 触发边沿或握手方式 | 上升沿、请求/应答位、流水号递增 | 防止重复检测同一座椅。 |
| `trigger_id` 来源 | PLC 流水号、C++ 自增、扫码绑定 | 写入共享内存并用于日志追踪。 |
| `seat_id` 来源 | 扫码枪、PLC 字符串寄存器、MES | Python 结果校验会比对 seat_id。 |
| `sku` 来源 | PLC recipe code、MES、固定工位配置 | C++ 选择配方，Python 加载检测配置。 |
| OK/NG/RECHECK 输出点位 | `Q0.0/Q0.1/Q0.2` | 实现 `send_decision`。 |
| 输出保持时间和复位方式 | 保持 100 ms、等待 PLC ack 后复位 | 防止 PLC 漏读输出。 |
| 通信超时和断线策略 | 100 ms、3 次重连、停线 | 任何不确定状态不能输出 `OK`。 |

需要替换的 C++ 接口：

- `PlcClient::initialize(...)`
- `PlcClient::wait_trigger(...)`
- `PlcClient::send_decision(...)`
- `PlcClient::get_health()`

### 4.2 相机

必须提供：

| 参数 | 示例 | 用途 |
| --- | --- | --- |
| 厂商、型号、SDK 版本 | Hikrobot、Basler、Daheng、FLIR | 决定 `CameraDevice` 驱动实现和链接库。 |
| 相机序列号到 `camera_index` 映射 | `TOP_BACK -> SN123`、`EYE_IN_HAND -> SN999` | 共享内存用 `camera_index` 标识物理相机，用 `pose_id` 标识检测视角。 |
| 机位 ID | `TOP_BACK`、`TOP_CUSHION` | Python 端映射为 `CameraBundle`。 |
| 分辨率、像素格式、bit depth | `2448x2048 Mono8` | 写入 `LightFrameMeta`，影响 slot 大小。 |
| 触发模式 | 外触发、软件触发、连续采集 | 推荐生产使用外触发或相机曝光输出同步。 |
| 曝光输出配置 | Line1/StrobeOut、极性、延时、脉宽 | 用于触发频闪控制器。 |
| 曝光、增益、帧率限制 | `800 us`、`gain=1.0` | 需与光源轮次参数一致。 |
| 图像回调或取帧方式 | SDK callback、blocking grab | 实现 `capture` 和缺帧判断。 |
| buffer 数量和丢帧计数 | 8 buffers、dropped counter | 做健康检查和追溯。 |

需要替换的 C++ 接口：

- `CameraDevice::initialize(...)`
- `CameraDevice::arm(...)`
- `CameraDevice::capture(...)`
- `CameraDevice::get_health()`

`CameraDevice::simulate_exposure_output(...)` 目前只用于模拟；真实硬件中通常不需要软件调用它，而是由相机 Line 输出真实电信号给频闪或 IO 模块。

### 4.3 频闪光源控制器

必须提供：

| 参数 | 示例 | 用途 |
| --- | --- | --- |
| 控制器品牌、型号、SDK 或协议 | CCS、OPT、串口协议、EtherCAT 模块 | 决定 `LightController` 驱动实现。 |
| 通信方式 | RS232/RS485、EtherCAT、Modbus、IO、厂商 SDK | 实现初始化、配置和状态读取。 |
| `light_index` 到物理通道映射 | `1 -> CH1 DIFFUSE` | 决定哪个光源在第几轮闪。 |
| 触发输入线映射 | TriggerIn1、DI2、相机 Line1 | 决定硬触发接线。 |
| 输出模式 | 外部触发闪光、常亮、软件触发 | 生产推荐外部触发闪光。 |
| 电流或亮度范围 | 0-100%、0-255、mA | 对应 `current_percent`。 |
| 脉宽范围和最小间隔 | 10-1000 us、最小 5 ms | 约束 `exposure_us` 和节拍。 |
| 触发延时 | 0-100 us | 用于对齐相机曝光窗口。 |
| 状态回读 | ready、overcurrent、overheat、trigger missed | arm 后和触发后必须检查。 |
| 异常关闭命令 | all off、clear alarm | 实现 `shutdown_all()`。 |

需要替换的 C++ 接口：

- `LightController::initialize(...)`
- `LightController::prepare_sequence(...)`
- `LightController::set_channel(...)`
- `LightController::arm_hardware_trigger(...)`
- `LightController::notify_hardware_triggered(...)`
- `LightController::shutdown_all()`
- `LightController::get_health()`

真实硬件中，`notify_hardware_triggered(...)` 可以有两种实现：

- 控制器支持触发计数或状态回读：读取“本通道已触发”并校验。
- 控制器不支持触发回读：读取 ready/fault 状态，并由相机图像质量和缺帧判断兜底。

### 4.4 机器人与位置触发（机器人飞拍）

机器人飞拍方案必须额外提供：

| 参数 | 示例 | 用途 |
| --- | --- | --- |
| 机器人品牌、型号、控制器版本 | FANUC M-20iD/25、R-30iB | 决定 `RobotClient` 驱动实现。 |
| AOI 启动/允许信号 | `AOI_START`、安全区互锁 | C++/PLC 启动机器人飞拍节拍。 |
| 机器人 READY/FAULT 信号 | `ROBOT_READY`、`ROBOT_FAULT` | 进入采集前做安全握手。 |
| pose 到位/过点信号 | `READY_T1`、`READY_T2` | 匹配 `pose.<N>.pose_id`。 |
| SHOT_ID 来源 | `SHOT_ID_T1` | 防止触发错序和结果追溯。 |
| PHOTO_TRIGGER 信号 | `PHOTO_TRIGGER_T1` | 位置触发或拍照触发输入。 |
| TCP 位姿和标定版本 | xyz/rpy、`calib/t1_robot_v1` | 写入共享内存并让 Python 选择对应 ROI/标定。 |

需要替换的 C++ 接口：

- `IRobotClient::initialize(...)`
- `IRobotClient::wait_pose_ready(...)`
- `IRobotClient::get_health()`

任意机器人未到位、FAULT、SHOT_ID 异常或位置触发超时都必须返回 `RobotFault`，最终输出 `RECHECK` 或 `ERROR`。

## 5. 光源、相机和机器人如何协同工作

推荐生产时序：

```text
1. PLC 触发座椅到位
2. C++ 生成 sequence_id 并读取 seat_id/sku
3. C++ 根据 capture_mode 生成检测视角计划
   - 固定机位：每台相机一个视角，pose_id 默认等于 camera_id
   - 机器人飞拍：每个 pose.<N> 一个视角，读取 READY/SHOT_ID/FAULT/PHOTO_TRIGGER
4. C++ 加载 light_order，例如 1,2,3,4
5. 对当前检测视角和 light_index=1：
   a. 配置频闪 CH1 的电流、脉宽、外触发模式
   b. arm 频闪 CH1
   c. arm 当前视角对应相机，等待下一次触发
   d. PLC/IO/相机曝光输出产生硬触发
   e. CH1 在相机曝光窗口内闪烁
   f. C++ 收当前视角在 CH1 下的图
6. 对 light_index=2/3/4 重复第 5 步
7. 当前视角完成全部光源后切换到下一个视角
8. C++ 校验 frame_count == view_count * light_count，且帧顺序必须符合“当前视角全光源→下一视角”
9. C++ 写共享内存
10. Python detector 检测
11. C++ 输出 PLC 决策
```

C++ 主控还会记录 `trace_root/cpp_controller_events.jsonl`，用于按 `sequence_id` 和 `trigger_id` 复盘采集失败、detector 超时、结果校验失败和 PLC 输出失败。

当前模拟代码中，`camera_exposure_output` 模式按以下顺序执行：

```text
prepare_sequence
  -> arm_hardware_trigger(light)
  -> CameraWorker::arm(current camera)
  -> CameraWorker::simulate_exposure_output(current camera)
  -> notify_hardware_triggered(light)
  -> wait_frame(current camera)
```

真实设备接入后，建议用硬件完成第 3 到第 5 步的精确同步：C++ 只负责提前配置和 arm，等待图像和读取故障状态。

## 6. 怎么控制哪个光源何时闪

### 6.1 控制顺序

`light_order` 决定光源轮次顺序：

```ini
light_order=1,2,3,4
```

含义：

```text
第 0 轮：light_index=1
第 1 轮：light_index=2
第 2 轮：light_index=3
第 3 轮：light_index=4
```

Python 端会把每张图的 `light_index` 映射为光源 ID，例如：

```text
1 -> DIFFUSE
2 -> POLAR_DIFFUSE
3 -> HIGH_LEFT
4 -> HIGH_RIGHT
```

如果现场通道不是这个顺序，不要改 Python 控制硬件；应在 C++ 光源通道映射中把 `light_index` 映射到真实物理通道，或者同步更新配方和 Python 映射。

### 6.2 控制闪烁时机

推荐由硬件控制闪烁时机，而不是由 C++ `sleep` 或软件命令卡时间。

推荐接线：

```text
Camera ExposureOut / StrobeOut
  -> Strobe Controller TriggerIn
  -> Light Channel Output
```

或：

```text
PLC / IO pulse
  -> Camera TriggerIn
  -> Camera ExposureOut
  -> Strobe Controller TriggerIn
```

C++ 控制的是“哪一轮 arm 哪个通道”，真实点亮时刻由曝光输出电信号决定：

```text
C++ arm CH3
相机下一次曝光开始
相机 ExposureOut 变为有效
频闪控制器收到 TriggerIn
CH3 在曝光窗口内闪
C++ 收 CH3 对应图像
```

### 6.3 控制曝光、亮度和脉宽

当前 `LightChannelParam` 包含：

| 字段 | 当前含义 |
| --- | --- |
| `light_index` | 逻辑光源编号。 |
| `physical_channel` | 频闪控制器真实物理通道。 |
| `exposure_us` | 当前模拟中也写入图像元数据；真实接入时应与相机曝光和频闪脉宽策略一致。 |
| `strobe_width_us` | 频闪输出脉宽。 |
| `trigger_delay_us` | 硬触发输入到频闪输出的延时。 |
| `gain` | 相机增益或该光源轮次使用的增益。 |
| `current_percent` | 频闪亮度/电流百分比。 |

当前默认每个光源轮次使用：

```text
exposure_us=800
strobe_width_us=800
trigger_delay_us=0
gain=1.0
current_percent=60.0
```

当前运行配置已支持把每个 `light_index` 的参数放入 `station_runtime.example.conf`，例如：

```ini
light.1.physical_channel=1
light.1.exposure_us=800
light.1.strobe_width_us=600
light.1.trigger_delay_us=0
light.1.gain=1.0
light.1.current_percent=60

light.3.physical_channel=3
light.3.exposure_us=600
light.3.strobe_width_us=450
light.3.trigger_delay_us=20
light.3.gain=1.0
light.3.current_percent=75
```

## 7. 共享内存图像打包规则

一个座椅任务写入一个 frame slot，包含所有检测视角、所有光源图像：

```text
frame_count = view_count * light_order.size()
```

每张图都有 `LightFrameMeta`：

| 字段 | 用途 |
| --- | --- |
| `camera_index` | 物理相机编号。Python 用 `camera_id/pose_id` 组装 `CameraBundle`。 |
| `pose_index` / `pose_id` | 检测视角编号和 ID；固定机位下默认等于 `camera_id`，机器人飞拍下等于轨迹点。 |
| `light_index` | 光源编号。Python 用它映射光源 ID。 |
| `light_seq_index` | 本次任务中的光源轮次。 |
| `shot_id` / `robot_timestamp_us` | 机器人飞拍的拍照流水号和机器人时间戳；固定机位可为模拟值。 |
| `robot_tcp_xyz_mm` / `robot_rpy_deg` | 机器人 TCP 位姿追溯字段。 |
| `width/height/channels/stride_bytes` | 图像尺寸和内存布局。 |
| `pixel_format/bit_depth/color_order/dtype_code` | 图像格式。 |
| `timestamp_us` | C++ 收图时间戳。 |
| `exposure_us/gain` | 本光源轮次采集参数。 |
| `image_offset/image_size/image_crc32` | 图像载荷定位和校验。 |

真实相机分辨率提高后，必须重新计算 `frame_slot_size`：

```text
slot_size >= header + frame_meta_count * sizeof(LightFrameMeta) + 所有图像字节数
```

示例：

```text
4 个视角 * 4 个光源 * 2448 * 2048 * Mono8 ~= 80 MB
```

此时默认 16 MB 不够，必须同时调整 C++ 和 Python 的共享内存 slot 配置。

## 8. 真实硬件集成步骤

推荐按最小风险顺序集成：

1. 保持模拟 PLC 和模拟频闪，只接真实相机。
   - 验证每个 `camera_index` 图像尺寸、stride、像素格式和 timestamp。
   - 确认 Python 能收到所有机位图像。
2. 接频闪控制器，但先低频手动触发。
   - 验证 `light_index -> 物理通道` 映射。
   - 用示波器或控制器日志确认曝光窗口内点亮。
   - 验证 `shutdown_all()` 能关闭所有通道。
3. 开启 `camera_exposure_output` 硬触发链路。
   - 相机曝光输出接到频闪 TriggerIn。
   - 检查极性、延时和脉宽。
   - 每个光源轮次保存一组图，确认图像亮度和光源 ID 对应。
4. 接 PLC 触发输入。
   - 只读触发，不输出分拣信号。
   - 验证防重复触发、seat_id、sku、trigger_id。
5. 接 PLC OK/NG/RECHECK 输出。
   - 低速节拍，人工确认输出点位。
   - 任一异常必须输出 RECHECK 或 ERROR，不允许 OK。
6. 连续运行。
   - 先 30 分钟，再 2 小时，再 8 小时。
   - 统计缺帧、光源故障、PLC 通信超时、共享内存 slot 泄漏和 detector timeout。

## 9. 必须验证的失败场景

上线前必须验证：

| 场景 | 期望行为 |
| --- | --- |
| Python detector 不启动 | C++ 等待结果超时，输出 `RECHECK`。 |
| 任一相机缺帧 | C++ 输出 `RECHECK`，错误码为 `MissingFrame`。 |
| 相机 arm 失败 | C++ 输出 `RECHECK`，错误码为 `CameraFault`。 |
| 频闪控制器 arm 失败 | C++ 输出 `RECHECK`，错误码为 `LightFault`。 |
| 曝光输出或硬触发确认失败 | C++ 输出 `RECHECK`，错误码为 `TriggerSyncFault`。 |
| 频闪配置缺失或非法 | C++ 输出 `RECHECK`，错误码为 `ConfigurationError`。 |
| 频闪故障状态回读异常 | C++ 输出 `RECHECK` 或 `ERROR`。 |
| PLC 触发协议错误 | C++ 不启动检测或输出 `RECHECK`。 |
| PLC 输出失败 | C++ 报设备故障，不把结果当作 OK 完成。 |
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

## 10. 接入代码位置

真实硬件驱动建议保持现有类名和职责，逐步替换内部实现：

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| PLC | `cpp_controller/src/control/plc_client.cpp` | 触发读取、结果输出、PLC 健康状态。 |
| 频闪 | `cpp_controller/src/control/light_controller.cpp` | 通道参数、arm、触发状态、关闭输出。 |
| 相机 | `cpp_controller/src/camera/camera_device.cpp` | 初始化、arm、采图、图像元数据。 |
| 采集编排 | `cpp_controller/src/control/frame_assembler.cpp` | 光源轮次、当前机位串行等待、结构化失败处理。 |
| 主流程 | `cpp_controller/src/control/station_controller.cpp` | 发布共享内存、等 Python 结果、PLC 输出。 |

真实 SDK 接入时不要把深度学习推理放进 C++；Python detector 仍然负责质量门禁、预处理、模型推理、融合和规则判定。

## 11. 现场参数记录模板

建议每台测试机保存一份现场参数表：

```text
工位名称：
PLC 型号：
PLC 通信方式：
触发输入点位：
OK 输出点位：
NG 输出点位：
RECHECK 输出点位：

camera_index=0:
  camera_id:
  serial:
  exposure_output_line:
  trigger_input_line:
  width:
  height:
  pixel_format:

light_index=1:
  light_name:
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
