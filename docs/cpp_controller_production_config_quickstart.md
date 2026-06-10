# C++ 主控生产配置快速上手

本文给不熟悉 C++ 代码的人使用。你只需要先把现场硬件信息填进配置文件，再运行配置校验；真正接入厂商 SDK 时，再由 C++ 工程师按同一份配置实现对应 backend。

## 1. 先确认当前边界

当前仓库已经具备：

- C++ 主控流程、共享内存 IPC、相机/频闪/PLC 抽象接口。
- 模拟链路，可用 `station_runtime.example.conf` 跑通端到端验证。
- 生产配置模板 `cpp_controller/config/station_runtime.production.example.conf`。
- 生产配置校验命令 `--validate-config`。
- 非模拟 backend 的 fail-fast 保护：如果还没有链接真实 SDK，程序会明确报错，不会偷偷用模拟硬件跑生产。

当前仓库还没有内置任何具体厂商 SDK，例如海康 MVS、Basler pylon、西门子 S7、某品牌频闪控制器串口协议等。拿到现场型号和 SDK 后，需要 C++ 工程师在 `cpp_controller/include/control/hardware_factory.hpp` 对应 backend 下接入真实驱动。

## 2. 复制生产模板

```bash
cp cpp_controller/config/station_runtime.production.example.conf \
   cpp_controller/config/station_runtime.production.conf
```

不要直接改 `station_runtime.production.example.conf`。以后升级仓库时，示例文件可能会变化。

## 3. 填写硬件模式

生产配置必须是：

```ini
hardware_mode=production
```

然后选择三类硬件 backend：

```ini
plc.backend=modbus_tcp
camera.backend=hikrobot_mvs
light.backend=serial_ascii
```

常用选择：

| 设备 | 可选值 | 什么时候用 |
| --- | --- | --- |
| PLC | `modbus_tcp` | PLC 通过 Modbus TCP 寄存器/线圈通信 |
| PLC | `siemens_s7` | 西门子 S7 通信 |
| PLC | `ethercat_io` | EtherCAT IO 模块 |
| PLC | `digital_io` | 普通 IO 卡输入输出 |
| 相机 | `hikrobot_mvs` | 海康机器人工业相机 MVS SDK |
| 相机 | `basler_pylon` | Basler pylon SDK |
| 相机 | `daheng_galaxy` | 大恒 Galaxy SDK |
| 相机 | `flir_spinnaker` | FLIR Spinnaker SDK |
| 频闪 | `serial_ascii` | 串口 RS232/RS485 文本协议 |
| 频闪 | `modbus_tcp` | 网口 Modbus TCP 控制器 |
| 频闪 | `ethercat_io` | EtherCAT 控制器或 IO 模块 |
| 频闪 | `digital_io` | 只用 IO 线选择/触发通道 |
| 任意 | `vendor_sdk` / `custom_sdk` | 供应商私有 SDK 或现场定制协议 |

## 4. 填 PLC 信息

找电气工程师或 PLC 工程师要以下信息，然后填到配置里：

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

字段说明：

| 字段 | 含义 | 填写示例 |
| --- | --- | --- |
| `plc.host` | PLC IP 地址 | `192.168.1.10` |
| `plc.port` | 通信端口 | Modbus TCP 常用 `502` |
| `plc.station_id` | 工位名，方便日志定位 | `LINE1_AOI_01` |
| `plc.trigger_source` | 座椅到位触发点位 | `DI0` / `I0.0` / `coil100` |
| `plc.trigger_id_source` | PLC 流水号来源 | `HR100` |
| `plc.seat_id_source` | 座椅条码来源 | `HR120` 或扫码枪缓存 |
| `plc.sku_source` | SKU/配方来源 | `HR160` |
| `plc.ok_output` | OK 输出点位 | `DO0` / `Q0.0` |
| `plc.ng_output` | NG 输出点位 | `DO1` |
| `plc.recheck_output` | 复检输出点位 | `DO2` |
| `plc.ack_input` | PLC 已读取结果的确认点位 | `DI1` |
| `plc.output_hold_ms` | 输出保持时间 | `200` |

生产要求：PLC 断线、触发超时、结果输出失败都不能输出 OK。

## 5. 填相机信息

每台相机一组 `camera.<N>.*`。`N` 是 C++ 写入共享内存的机位编号，Python 会用它映射到检测配方。

```ini
camera.0.camera_id=TOP_BACK
camera.0.serial_number=DA12345678
camera.0.width=2448
camera.0.height=2048
camera.0.channels=1
camera.0.pixel_format=Mono8
camera.0.trigger_line=Line0
camera.0.exposure_output_line=Line1
camera.0.buffer_count=8
```

字段说明：

| 字段 | 含义 | 从哪里拿 |
| --- | --- | --- |
| `camera_id` | 机位名称 | 现场相机布局图 |
| `serial_number` | 相机序列号 | 厂商相机工具 |
| `width` / `height` | 图像分辨率 | 相机配置工具 |
| `channels` | 通道数，Mono 通常为 1 | 像素格式 |
| `pixel_format` | 像素格式 | `Mono8` / `Mono12` / `BayerRG8` |
| `trigger_line` | 相机触发输入线 | 接线图或相机 IO 配置 |
| `exposure_output_line` | 曝光输出/StrobeOut 线 | 用于触发频闪 |
| `buffer_count` | SDK 图像缓冲数量 | 建议 8 起步 |

如果有 4 台相机，就继续增加 `camera.2.*`、`camera.3.*`。

## 6. 填频闪控制器信息

串口控制器示例：

```ini
light.backend=serial_ascii
light.device_id=OPT_CONTROLLER_01
light.serial_port=/dev/ttyUSB0
light.baud_rate=115200
light.trigger_input_line=TriggerIn1
```

网口或 SDK 控制器可以填：

```ini
light.backend=vendor_sdk
light.device_id=STROBE_01
light.host=192.168.1.20
light.port=4001
light.trigger_input_line=TriggerIn1
```

字段说明：

| 字段 | 含义 |
| --- | --- |
| `light.device_id` | 控制器名称或序列号 |
| `light.serial_port` | Linux 串口，如 `/dev/ttyUSB0` |
| `light.baud_rate` | 串口波特率 |
| `light.host` / `light.port` | 网口控制器地址 |
| `light.trigger_input_line` | 频闪触发输入线，通常来自相机 ExposureOut |

## 7. 填光源通道映射

`light_order=1,2,3,4` 表示每个机位按 1、2、3、4 的逻辑光源顺序采图。每个逻辑光源必须映射到真实控制器物理通道：

```ini
light.1.physical_channel=1
light.1.exposure_us=800
light.1.strobe_width_us=700
light.1.trigger_delay_us=10
light.1.gain=1.0
light.1.current_percent=60
```

字段说明：

| 字段 | 含义 | 注意 |
| --- | --- | --- |
| `physical_channel` | 频闪控制器输出通道 | 必须和接线一致 |
| `exposure_us` | 相机曝光时间 | 由成像效果和节拍决定 |
| `strobe_width_us` | 频闪脉宽 | 应小于或等于曝光窗口 |
| `trigger_delay_us` | 触发延时 | 用于对齐曝光窗口 |
| `gain` | 相机增益 | 过高会放大噪声 |
| `current_percent` | 光源电流/亮度百分比 | 不得超过控制器和光源规格 |

## 8. 校验配置

构建 C++：

```bash
cmake -S cpp_controller -B cpp_controller/build
cmake --build cpp_controller/build
```

只检查配置，不启动硬件：

```bash
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.production.conf \
  --validate-config
```

看到下面输出，说明配置字段齐全：

```text
C++ station runtime config OK: cpp_controller/config/station_runtime.production.conf
```

如果还看到 `TODO`、空字段、端口为 0、生产模式仍使用 simulated backend，校验会失败并指出具体字段。

## 9. 真实驱动接入点

配置校验通过后，还需要 C++ 工程师实现真实驱动：

- PLC：`cpp_controller/include/control/iplc_client.hpp`
- 相机：`cpp_controller/include/camera/icamera.hpp`
- 频闪：`cpp_controller/include/control/ilight_controller.hpp`
- backend 工厂：`cpp_controller/include/control/hardware_factory.hpp`

接入前，运行非 simulated backend 会失败并提示“尚未链接真实硬件驱动”。这是保护机制，避免误上线。

## 10. 上线前检查清单

- `--validate-config` 通过。
- PLC 触发点位、输出点位、ack 点位在手动 IO 测试中正确。
- 相机序列号和 `camera_index` 对应现场机位。
- 每个光源逻辑编号和物理通道接线一致。
- 相机 ExposureOut 到频闪 TriggerIn 的线已接好，极性正确。
- `strobe_width_us <= exposure_us`，电流不超过光源规格。
- Python detector 常驻运行，C++ 和 Python 协议校验通过。
- 故障注入和断线测试都不会输出 OK。
