# Seat Surface AOI — C++ Controller

## 概述

`cpp_controller` 是座椅表面 AOI 检测系统的 C++ 主控程序，负责工位流程编排、固定机位/机器人飞拍采集调度、外部信号/机器人信号交互，以及通过跨平台共享内存与 Python 检测引擎进行 IPC 通信。Linux/macOS 使用 POSIX 共享内存，Windows 工控机使用 Named Shared Memory。

### 核心定位

```
┌──────────────────────────────────────────────────────────────────┐
│                     C++ Controller (本工程)                       │
│                                                                  │
│   外部信号/Robot 触发 ─→ 光源时序 ─→ 相机采图 ─→ FrameRingBuffer (SHM) │
│                                          │                       │
│                                          ↓                       │
│                                   [Python 检测器]                 │
│                                          │                       │
│                                          ↓                       │
│   外部信号结果 ←────────────── ResultRingBuffer (SHM)                │
└──────────────────────────────────────────────────────────────────┘
```

---

## 目录结构

```
cpp_controller/
├── CMakeLists.txt                          # CMake 构建配置 (C++17)
│
├── src/
│   ├── main.cpp                            # 主入口，命令行参数解析与主循环
│   │
│   ├── ipc/                                # IPC 共享内存通信层
│   │   ├── shared_memory_posix.cpp         # Linux/macOS POSIX shm_open/mmap 封装
│   │   ├── shared_memory_win32.cpp         # Windows CreateFileMapping/MapViewOfFile 封装
│   │   ├── crc32.cpp                       # CRC32 校验和计算
│   │   ├── frame_ring_buffer.cpp           # 图像帧环形缓冲区（C++ → Python）
│   │   └── result_ring_buffer.cpp          # 检测结果环形缓冲区（Python → C++）
│   │
│   ├── control/                            # 工位控制逻辑层
│   │   ├── signal_client.cpp                 # 外部信号客户端（模拟实现）
│   │   ├── robot_client.cpp                # 机器人位姿/SHOT_ID 客户端（模拟实现）
│   │   ├── light_controller.cpp            # 光源控制器（模拟实现）
│   │   ├── hardware_backend.cpp            # 硬件模式和 backend 解析
│   │   ├── station_controller.cpp          # 工位主控协调器（核心编排）
│   │   ├── station_health.cpp              # 连续复检和健康报警状态
│   │   ├── production_event_log.cpp        # C++ 生产事件 JSONL
│   │   ├── image_writer.cpp                # PGM 原图落盘、日期分目录和低容量旧数据清理
│   │   ├── frame_assembler.cpp             # 固定机位/机器人飞拍采集编排器
│   │   └── station_runtime_config.cpp      # 运行时配置文件解析
│   │
│   └── camera/                             # 相机模拟层与 Hikrobot MVS 适配层
│       ├── camera_device.cpp               # 模拟相机设备（生成合成图像）
│       ├── hikrobot_mvs_camera.cpp         # 海康 MVS SDK 相机适配，需显式启用 SDK 构建
│       └── camera_worker.cpp               # 相机工作线程封装
│
├── include/                                # 头文件（与 src 目录对应）
│   ├── common/
│   │   ├── inspection_types.hpp            # 检测相关结构体定义
│   │   ├── error_code.hpp                  # 错误码枚举
│   │   ├── string_utils.hpp                # C 风格字符串工具函数
│   │   └── time_utils.hpp                  # 微秒级时间戳工具
│   ├── ipc/
│   │   ├── shm_protocol.hpp                # 共享内存协议定义（帧头、槽位头）
│   │   ├── shared_memory.hpp               # SharedMemory RAII 封装
│   │   ├── frame_ring_buffer.hpp           # FrameRingBuffer + CapturedFrame/SeatImageBundle
│   │   ├── result_ring_buffer.hpp          # ResultRingBuffer + InspectionResultPayload
│   │   └── crc32.hpp                       # CRC32 函数声明
│   └── control/
│       ├── station_controller.hpp          # StationController + StationConfig
│       ├── isignal_client.hpp               # 外部信号抽象接口
│       ├── signal_client.hpp                # ExternalSignalClient + SimSignalClient + ManualSignalClient
│       ├── irobot_client.hpp               # Robot 抽象接口
│       ├── robot_client.hpp                # SimRobotClient
│       ├── hardware_factory.hpp            # 模拟/生产 backend 工厂
│       ├── hardware_backend.hpp            # 硬件模式和 backend 枚举
│       ├── light_controller.hpp            # LightController + 光源类型定义
│       ├── image_writer.hpp                # C++ 原图落盘路径和清理接口
│       ├── frame_assembler.hpp             # FrameAssembler + Recipe
│       ├── external_trigger.hpp            # ExternalTrigger 触发数据结构
│       ├── station_runtime_config.hpp      # StationRuntimeConfig + 配置解析
│       └── station_health.hpp              # 工位健康状态
│   └── camera/                             # 相机相关头文件
│       ├── icamera.hpp                     # 相机抽象接口
│       ├── camera_device.hpp               # CameraDevice + CameraConfig/CameraHealth
│       └── camera_worker.hpp               # CameraWorker
│
├── tools/
│   ├── protocol_layout.cpp                 # 打印所有协议结构体大小的诊断工具
│   └── ipc_safety_checks.cpp               # IPC 故障注入与安全测试套件
│
└── config/
    ├── station_runtime.test.conf           # 工控机手动触发联调：真实相机 + 真实 FL-ACDH
    └── station_runtime.production.conf     # 生产运行：TCP 信号 + 真实相机 + 真实 FL-ACDH
```

---

## 编译产物

| Target | 类型 | 说明 |
|--------|------|------|
| `seat_aoi_ipc` | 静态库 | 共享内存、环形缓冲区、CRC32 |
| `seat_aoi_control` | 静态库 | 外部信号、光源、相机、工位编排 |
| `seat_aoi_controller` | 可执行文件 | 主程序入口 |
| `protocol_layout` | 可执行文件（工具） | 打印结构体大小，用于跨语言对齐校验 |
| `ipc_safety_checks` | 可执行文件（测试） | IPC 故障注入与安全测试 |

### 依赖关系

```
seat_aoi_ipc (无外部依赖，Linux/macOS 链接 pthread/rt，Windows 使用 Win32 API)
       ↑
seat_aoi_control (依赖 seat_aoi_ipc)
       ↑
seat_aoi_controller (依赖 seat_aoi_control)
ipc_safety_checks   (依赖 seat_aoi_control)
```

默认构建外部依赖为零，仅依赖 C++17 标准库和系统 API：Linux/macOS 使用 `shm_open`/`mmap`，Windows 使用 `CreateFileMappingW`/`MapViewOfFile`；TCP 信号客户端在 Windows 上链接系统库 `ws2_32`（WinSock2），Linux 链接 `rt`/`pthread`。`hikrobot_mvs` 相机 backend 已预留真实 MVS SDK 适配层，但必须在工控机上显式启用 SDK 构建；外部信号网关和频闪真实 backend 仍需按现场协议继续接入。

---

## 硬件模式说明

当前支持三种运行模式：

| 模式 | 配置 | 用途 |
|------|------|------|
| 模拟模式 | `hardware_mode=simulated` | 不需要真实硬件，使用模拟外部信号、模拟相机、模拟频闪跑通端到端 IPC 和故障注入 |
| 实验室联调模式 | `hardware_mode=lab` | 外部信号网关未接入前使用 `signal.backend=manual_trigger`，配合真实相机/频闪 backend 做工控机手动触发联调 |
| 生产模式 | `hardware_mode=production` | 强制填写外部信号、相机、频闪现场参数，禁止误用 simulated backend；当前仓库提供配置校验和 fail-fast 保护，真实 SDK 需按型号接入 |

模拟模式行为：

| 模块 | 模拟行为 | 故障注入 |
|------|---------|---------|
| **Signal** (`signal_client.cpp`) | 自动生成虚拟触发信号（`SIM_SEAT_XXX`），递增 trigger_id | `--simulate-signal-result-fault` 模拟结果发布失败<br>`--simulate-trigger-timeout` 模拟触发超时 |
| **Robot** (`robot_client.cpp`) | 在机器人飞拍模式下模拟 pose ready、SHOT_ID、TCP 位姿和机器人时间戳 | `simulate_robot_fault=true` 模拟机器人 FAULT |
| **相机** (`camera_device.cpp`) | 生成合成图像（纹理 + 梯度 + 光源差异），并按当前 `light_index/light_seq_index` 写入帧元数据，64×48 可配置分辨率 | `--simulate-missing-frame` 模拟丢帧 |
| **光源** (`light_controller.cpp`) | 模拟频闪时序，1ms sleep 模拟硬件延迟 | `--simulate-light-fault` 模拟光源故障 |

当前支持的配置 backend 名称：

| 设备 | 可选 backend | 已实现 |
|------|--------------|--------|
| Signal | `simulated`、`manual_trigger`、`external_signal`、**`tcp_signal`**、**`distance_trigger`**、`modbus_tcp`、`siemens_s7`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` | ✅ `simulated`、`manual_trigger`、`external_signal`、**`tcp_signal`**、**`distance_trigger`** |
| 相机 | `basler_pylon`、**`hikrobot_mvs`**、`daheng_galaxy`、`flir_spinnaker`、`vendor_sdk`、`custom_sdk` | ✅ `simulated`、**`hikrobot_mvs`**（含 Line0 硬件触发） |
| 频闪 | **`serial_ascii`**、`modbus_tcp`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` | ✅ `simulated`、**`serial_ascii`**（FL-ACDH RS232，**多控制器支持**） |
| Robot | `modbus_tcp`、`siemens_s7`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` | ✅ `simulated` |

`manual_trigger` 只能用于 `hardware_mode=lab`，它生成测试触发并记录结果，不输出真实外部 IO。`production` 模式要求 `signal.backend=external_signal` 或 `tcp_signal`，禁止 `manual_trigger` 和 `simulated` backend。如果选择了非 simulated backend，但 C++ 尚未链接对应真实驱动，程序会在初始化阶段明确报错并退出，不会偷偷回退到模拟硬件。

### TCP 信号客户端 (`tcp_signal`)

当 `signal.backend=tcp_signal` 时，C++ 控制器作为 TCP 服务端在 `signal.port`（默认 9000）上监听，接收 PLC 发送的 SN 触发行：

- **裸 SN 模式**（`signal.delimiter=""`）：接收 `SN\n`，回复 `ok\n`
- **分隔符模式**（`signal.delimiter="|"`）：接收 `start|SN\n`，回复 `ok\n`

### FL-ACDH 频闪控制器 (`serial_ascii`)

当 `light.backend=serial_ascii` 时，通过 RS232 串口（`light.serial_port`、`light.baud_rate`，默认 9600 8N1）与 FL-ACDH-20048-4 通信，使用 XOR 校验和的专有 ASCII 帧协议。
默认 `light.response_mode=ack` 会要求每条命令收到控制器返回的 `$` ACK；如果现场控制器确认不回包，或联调阶段只有 PC TX/GND 单向接线，可显式设置 `light.response_mode=none`，程序只校验串口写入成功，后续仍依赖相机取帧/示波器确认触发。未知应答、写入失败或取帧超时仍会保守输出 `RECHECK`。

### 多控制器频闪

`light` 配置支持单控制器和多控制器两种模式：

**单控制器（兼容旧格式）**：
```ini
light.backend=serial_ascii
light.serial_port=/dev/ttyUSB0
light.response_mode=ack
light.1.physical_channel=1    # light.<N>.<field> → controller 0
```

**多控制器（新格式）**：
```ini
light.0.backend=serial_ascii
light.0.serial_port=/dev/ttyUSB0
light.0.baud_rate=115200
light.0.response_mode=ack
light.0.trigger_input_line=Line1
light.0.1.physical_channel=1  # light.<M>.<N>.<field>：控制器 M, 光源索引 N
light.1.backend=serial_ascii
light.1.serial_port=/dev/ttyUSB1
light.1.baud_rate=115200
light.1.response_mode=ack
light.1.trigger_input_line=Line1
light.1.3.physical_channel=1  # 第二台控制器通道 1, 光源索引 3
```

每个 `RuntimeLightChannelConfig` 记录 `controller_index`，`FrameAssembler` 内部管理多个 `ILightController` 实例，按控制器分别 `prepare_sequence()`，再按通道的 `controller_index` 派发触发。生产校验会拒绝引用未配置控制器的 `light.<M>.<N>`，避免运行期越界或漏触发。

### TCP 结果回传

`TcpSignalClient::publish_result()` 支持通过 TCP 回传检测结果：

```ini
signal.result_host=192.168.1.100     # 结果通知目标 IP
signal.result_port=9001              # 结果通知目标端口
signal.result_prefix=result          # 报文前缀
signal.result_delimiter=|            # 字段分隔符
signal.ok_text=OK                    # OK 文本
signal.ng_text=NG                    # NG 文本
signal.recheck_text=RECHECK          # RECHECK 文本
signal.error_text=ERROR              # ERROR 文本
```

发送格式：`result|seat_id|OK\n`。优先使用 `result_host:result_port`，回退复用 PLC 连接，再回退仅日志。

### 图像落盘 (PGM)

```ini
image_save.enabled=true              # 启用存图
image_save.root_dir=images           # 存储根目录
image_save.save_original=true        # 保存采集原图
image_save.cleanup_enabled=true      # 检测前做业务存储低水位治理
image_save.cleanup_min_free_ratio=0.20 # 可用容量低于 20% 时触发清理
image_save.cleanup_trace_root=true   # 同步清理 trace/YYYYMMDD 历史检测样本
image_save.fail_on_save_error=true   # 原图落盘失败时输出 RECHECK/DeviceFault
```

采集成功后自动保存 PGM 格式原图：`{root_dir}/YYYYMMDD/{seat_id}/{camera_id}_{timestamp}_L{light_index}_original.pgm`。PGM (P5 binary) 纯 C++ 实现，无需外部库依赖。默认配置下存图失败会保守输出 `RECHECK/DeviceFault`，不继续把当前件当作正常检测结果处理。

启用清理后，C++ 主控会在每次检测前检查 `image_save.root_dir` 与 `trace_root` 所在磁盘的可用容量比例；低于 `cleanup_min_free_ratio` 时，只扫描 `YYYYMMDD` 日期目录下的业务文件，按文件最后修改时间从早到晚逐个删除，直到容量恢复或没有可清理文件。非日期目录、`display_latest.json`、前端操作员日志和其它配置文件不会被扫描删除。清理后容量仍低于阈值会直接输出 `RECHECK/DeviceFault`，避免磁盘写满后继续运行。

`image_save.fail_on_save_error=true` 时，启用原图落盘后任何 PGM 写入失败都会让当前任务输出 `RECHECK/DeviceFault`。如果仅作临时调试且允许丢原图，可以显式改为 `false`，但生产追溯场景建议保持默认值。

### JSON 详细结果输出

```ini
json_output.enabled=true             # 启用 JSON 输出
json_output.host=192.168.1.100       # 目标 IP
json_output.port=9002                # 目标端口
```

检测完成后通过 TCP 发送单行 JSON：
```json
{"type":"inspection_result","sn":"ABC123","overall":"OK","overall_code":1,"sequence":5,"error_code":0,"elapsed_ms":123.4,"defect_count":0}
```
发送失败不阻断主流程。

### 距离传感器触发 (`distance_trigger`)

当 `signal.backend=distance_trigger` 时，使用 JK-LRD 激光测距传感器（RS485 Modbus RTU）作为触发源：

```ini
signal.backend=distance_trigger
signal.port=9000                    # 上游 TCP 信号端口（接收 SN）
signal.trigger_queue_path=COM4,9600,1,500,500,50
# 格式：串口,波特率,从站地址,阈值mm,消抖ms,轮询间隔ms
```

内部架构：
- `DistanceSensor`：Modbus RTU (CRC-16) 轮询距离值，触发消抖状态机
  - **ARMED** → 距离 < 阈值 → **Debouncing** → 持续阈值下超限 → **TRIGGERED**
  - **TRIGGERED** → 距离 >= 阈值 + 2s 冷却 → **ARMED**（重新就绪）
- `DistanceTriggerSignalClient`：包装上游 `TcpSignalClient`（SN 接收）+ `DistanceSensor`（触发），触发时用缓存的 SN 组装 `ExternalTrigger`

## 采集方案模式

当前主控通过 `capture_mode` 支持两类采集方案，二者共用相同的 C++ 控制边界、共享内存协议和 Python 检测链路：

| 模式 | 配置值 | 视角定义 | 典型配置 |
|------|--------|----------|----------|
| 固定机位多光源 | `capture_mode=fixed_camera` | 每个 `camera.<N>` 自动生成一个检测视角，`pose_id` 默认等于 `camera_id` | `config/station_runtime.test.conf`、`config/station_runtime.production.conf` |

当前 `cpp_controller/config` 不再维护机器人飞拍配置文件，本轮现场接线只按固定双机位三频闪链路验证。

固定机位还可通过 `capture_schedule` 选择采集调度：

| 调度 | 配置值 | 行为 |
|------|--------|------|
| 视角串行 TDM | `view_serial_tdm` | 当前视角完成全部 `light_order` 后再切换下一视角，兼容旧配置。 |
| 共享光源并行 | `shared_light_parallel` | 每一路共享光源频闪前先 arm 所有固定机位相机，然后触发一次光源并分别收图；发布给 Python 的帧顺序仍保持“当前视角全光源→下一视角”。 |

机器人飞拍模式会在采集每个 pose 前调用 `RobotClient::wait_pose_ready()`，校验 READY/FAULT/SHOT_ID，并把 `pose_id`、`shot_id`、机器人时间戳和 TCP 位姿写入 `LightFrameMeta`。任何机器人未到位、FAULT、触发错序或超时都返回 `RobotFault`，不会输出 `OK`。

固定机位模式不会调用 `RobotClient`，采集器会为每个固定机位填充确定性的中性 pose 状态：`ready=true`、`fault=false`、`shot_id=trigger_id`、机器人时间戳和 TCP/RPY 为 0。这样固定机位链路不会因为未配置机器人 backend 被误阻断。

---

## 共享内存协议

### 两个环形缓冲区

| 共享内存名称 | 方向 | 用途 |
|-------------|------|------|
| `/seat_aoi_cpp_to_py_frames_v1` | C++ → Python | 图像帧数据：SeatJobMeta + LightFrameMeta[] + 图像字节 |
| `/seat_aoi_py_to_cpp_results_v1` | Python → C++ | 检测结果：InspectionResultMeta + DefectResultMeta[] |

### 内存布局

```
┌─────────────────────────────────────────────────────┐
│ ShmHeader (40 bytes)                                │
│   magic: 0x53414F49 ("SAOI")                        │
│   version: 2                                        │
│   slot_count / slot_size                            │
│   write_index / read_index / heartbeat              │
├─────────────────────────────────────────────────────┤
│ Slot[0]  (slot_size bytes)                          │
│   ┌─ FrameSlotHeader / ResultSlotHeader             │
│   ├─ LightFrameMeta[] / DefectResultMeta[] (meta区) │
│   └─ 图像字节 / 缺陷遮罩 (payload区)                  │
├─────────────────────────────────────────────────────┤
│ Slot[1] ... Slot[N-1]                               │
└─────────────────────────────────────────────────────┘
```

### 槽位状态机

```
Empty ─→ Writing ─→ Ready ─→ Reading ─→ Empty
  ↑                      ↓
  └──── Corrupted ←──────┘
```

### 关键结构体大小

| 结构体 | 大小 | 说明 |
|--------|------|------|
| `ShmHeader` | 40 bytes | 共享内存文件头 |
| `FrameSlotHeader` | 268 bytes | 帧槽位头（含 SeatJobMeta） |
| `ResultSlotHeader` | 140 bytes | 结果槽位头（含 InspectionResultMeta） |
| `LightFrameMeta` | 324 bytes | 单帧图像元数据，含 `camera_id`、`pose_id`、`shot_id` 和机器人位姿 |
| `SeatJobMeta` | 232 bytes | 作业元数据，含 `view_count` 与 `capture_mode` |
| `InspectionResultMeta` | 104 bytes | 检测结果元数据 |
| `DefectResultMeta` | 464 bytes | 单个缺陷描述，含 `camera_id` 与 `pose_id` |

### 初始化和结果回收安全策略

- Frame/Result ring 打开既有共享内存时会校验共享内存对象实际大小以及 `magic/version/slot_count/slot_size`。未显式 reset 且大小或布局不匹配时初始化失败，不会静默清零或重写可能属于另一进程的共享内存。Windows 下逻辑名会映射为 `Local\seat_aoi_cpp_to_py_frames_v1` 和 `Local\seat_aoi_py_to_cpp_results_v1`。
- Result ring 读取时要求 `payload_size == ResultSlotHeader + defect_count * DefectResultMeta`，且 slot 头与 `InspectionResultMeta.defect_count` 一致，防止缺陷数组截断或尾部脏数据被接受。
- 等待当前 `sequence_id` 时，旧序号的 `Ready/Corrupted/Timeout` slot 会被回收清空；当前序号的 `Corrupted` 或 `Timeout` slot 会立即转成 `CrcMismatch` 或 `DetectorTimeout`，不会继续等待到超时。
- detector 返回结果时会校验判定语义：`OK` 必须质量通过、无错误且无缺陷，`NG` 必须质量通过、无错误且存在缺陷；语义不一致的结果按 `InvalidPayload` 转为 `RECHECK`。
- detector 返回 `ERROR` 时，C++ 记录原始错误和健康状态，但发布给外部信号的动作映射为 `Recheck`，避免把检测侧不确定状态输出成产线 `OK`。

---

## 构建与运行

### 系统要求

- CMake ≥ 3.16
- C++17 编译器（GCC ≥ 8 / Clang ≥ 7 / MSVC Build Tools 2019+）
- Linux、macOS 或 Windows 工控机环境

### 构建

```powershell
cd cpp_controller
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

Windows 上可优先使用根目录跨平台入口，它会自动选择可用构建方式，不依赖 CMake 默认的 `NMake Makefiles`：

```powershell
uv run python tools/run_simulated_ipc.py
```

该入口会依次尝试 Ninja、已安装的 Visual Studio/MSBuild 生成器、`nmake`、MinGW Makefiles，以及 `clang++`/`g++` 直接编译回退路径；如果某个旧 build 缓存曾记录不可用生成器，或仓库从开发机复制到工控机后 `CMakeCache.txt` 里的源码/构建绝对路径仍指向旧目录，会在独立的 `cpp_controller/build/simulated-ipc/<generator>/` 子目录清理并重新生成，不复用根 build 缓存，也不会删除其它生成器或手工构建目录。当前 CMake 工程会在 MSVC 下自动添加 `/utf-8`，避免中文日志字符串在本地代码页下触发编译错误。

需要手动构建 MSVC 产物时，可进入 x64 VS 开发命令环境后显式指定生成器：

```powershell
& "C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\Common7\Tools\VsDevCmd.bat" -arch=x64 -host_arch=x64
cmake -S cpp_controller -B cpp_controller/build -G "NMake Makefiles" -DCMAKE_BUILD_TYPE=Release
cmake --build cpp_controller/build --config Release
```

`ipc_safety_checks` 使用跨平台临时目录生成运行配置，并会在 Windows Named Shared Memory 仍被进程持有时校验 slot 布局不匹配必须失败；测试结束后再释放 mapping，避免把 Windows “最后一个 handle 关闭即消失”的生命周期误判为协议允许重建。

Windows 工控机已安装海康 MVS 时，可启用 MV-CH120-20GC 真实相机 backend：

```powershell
cmake -S cpp_controller -B cpp_controller/build `
  -DCMAKE_BUILD_TYPE=Release `
  -DSEAT_AOI_ENABLE_HIKROBOT_MVS=ON `
  -DSEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR="C:/Program Files (x86)/MVS/Development/Includes" `
  -DSEAT_AOI_HIKROBOT_MVS_LIBRARY="C:/Program Files (x86)/MVS/Development/Libraries/win64/MvCameraControl.lib"
cmake --build cpp_controller/build --config Release
```

不启用 `SEAT_AOI_ENABLE_HIKROBOT_MVS` 时，`camera.backend=hikrobot_mvs` 会在初始化阶段明确报错，不会回退到模拟相机。

### 部署打包

根目录提供 `tools/package_release.py` 生成 Windows 离线部署包。脚本会先构建 C++ 主控，再把下列 C++ 相关内容放入包内：

- `bin/seat_aoi_controller`：已构建主控入口。
- `bin/protocol_layout`：协议结构体大小诊断工具。
- `bin/ipc_safety_checks`：共享内存故障注入与安全检查工具。
- `cpp_controller/`：源码、`CMakeLists.txt`、配置模板和工具源码，不包含 `build/` 缓存。

参考联调包：

```powershell
uv run python -m tools.package_release
```

生产包需要同时带真实 Python 模型资产：

```powershell
uv run python -m tools.package_release
```

执行前先把真实模型产物替换到根目录 `model/`，脚本会默认集成该目录。

解包后可执行 `./bin/seat_aoi_controller --config cpp_controller/config/station_runtime.test.conf --once --wait-ms 8000` 在工控机上做手动触发联调。C++ 仍只负责外部信号、相机、频闪、共享内存写入和结果读取，不包含深度学习推理。

### 运行

```powershell
# 单次检测（默认 --once 模式）
./build/seat_aoi_controller

# 循环模式（持续等待触发）
./build/seat_aoi_controller --loop

# 使用工控机手动触发联调配置
./build/seat_aoi_controller --config config/station_runtime.test.conf

# 只校验生产配置，不启动外部信号/相机/频闪
./build/seat_aoi_controller --config config/station_runtime.production.conf --validate-config

# 故障注入测试
./build/seat_aoi_controller --simulate-light-fault
./build/seat_aoi_controller --simulate-missing-frame
./build/seat_aoi_controller --simulate-signal-result-fault
./build/seat_aoi_controller --simulate-trigger-timeout

# 自定义参数
./build/seat_aoi_controller --max-jobs 10 --wait-ms 3000

# 清理共享内存后退出
./build/seat_aoi_controller --cleanup

# 运行诊断工具
./build/protocol_layout

# 运行 IPC 安全测试
./build/ipc_safety_checks

# Windows 工控机上机前交接预检
uv run python -m tools.validate_deployment_preflight
uv run python -m tools.validate_deployment_preflight --strict-production
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config <path>` | 运行时配置文件路径；未提供时使用内置 simulated fallback，默认 2 视角 × 4 路光源 | 无 |
| `--loop` | 持续循环模式 | 单次 |
| `--once` | 单次执行（覆盖 --loop） | 默认 |
| `--no-reset` | 不重置共享内存 | false |
| `--cleanup` | 仅清理共享内存后退出 | - |
| `--validate-config` | 只校验运行配置后退出，不初始化共享内存和硬件 | false |
| `--max-jobs <N>` | 最大检测任务数（0=不限） | 0 |
| `--wait-ms <N>` | 检测结果等待超时(ms) | 5000 |
| `--trigger-timeout-ms <N>` | 外部信号触发等待超时(ms) | 1000 |
| `--trace-root <path>` | C++ 生产事件日志目录 | trace |
| `--simulate-light-fault` | 模拟光源故障 | false |
| `--simulate-missing-frame` | 模拟相机丢帧 | false |
| `--simulate-signal-result-fault` | 模拟外部信号结果发布失败 | false |
| `--simulate-trigger-timeout` | 模拟外部信号触发超时 | false |

运行时配置中的布尔字段只接受 `true/false/1/0/yes/no/on/off`，拼写错误或未知布尔值会导致配置加载失败，避免故障注入或共享内存 reset 选项被静默解释为 `false`。

上机前建议先运行 `tools.validate_deployment_preflight`。默认模式确认当前仓库可实现的参考链路、Windows Named Shared Memory 映射、跨平台模拟 IPC、部署包入口和 `lab/manual_trigger` 联调路径无本地阻塞；`--strict-production` 用于正式放行，会把缺少正式 `production.conf` 或真实模型资产作为阻塞项。

### 运行时配置文件格式

```ini
# key=value，支持 # 注释
hardware_mode=simulated
signal.backend=simulated
camera.backend=simulated
light.backend=simulated
reset_shared_memory=true
slot_count=4
frame_slot_size=16777216
result_slot_size=65536
publish_timeout_ms=1000
detector_timeout_ms=5000
trigger_timeout_ms=1000
camera_timeout_ms=200
light_timeout_ms=200
warning_recheck_threshold=3
critical_recheck_threshold=5
max_jobs=1
recipe_id=seat_a_black_leather_v1
capture_mode=fixed_camera
light_order=1,2,3,4
trace_root=trace

# 相机配置；生产模式需要补充 serial_number、trigger_line、exposure_output_line
camera.0.camera_id=TOP_BACK
camera.0.width=64
camera.0.height=48
camera.0.channels=1
camera.0.pixel_format=Mono8
camera.0.buffer_count=8

# 逻辑光源到真实控制器物理通道和采集参数的映射
light.1.physical_channel=1
light.1.exposure_us=800
light.1.strobe_width_us=700
light.1.trigger_delay_us=10
light.1.gain=1.0
light.1.current_percent=60

# 故障注入
simulate_light_fault=false
simulate_missing_frame=false
simulate_signal_result_fault=false
simulate_trigger_timeout=false
```

当前目录只保留两份运行配置：

```powershell
./build/seat_aoi_controller --config config/station_runtime.test.conf --validate-config
./build/seat_aoi_controller --config config/station_runtime.production.conf --validate-config
```

该模板的 `recipe_id` 已对齐 Python 固定机位生产配方 `seat_a_black_leather_production_v1`。模型补齐后，Python detector 会按该配方启用 ONNX ROI、ECC、监督 ONNX、WideResNet50/PCA/PatchCore/FAISS safety net；相机 `calibration_id` 必须和 Python 标定文件保持一致。

当前固定机位生产配置按现场已确认硬件预置：Hikrobot MVS 相机 2 台，4096 x 3072，`Line0` 硬触发；FL-ACDH 光源控制器 1 台，通道 1/2/3 接三路共享频闪光源；相机触发线已经接在 FL-ACDH 同步触发输出上。生产配置启用 `capture_schedule=shared_light_parallel` 和 `light_order=1,2,3`，每路共享频闪光源点亮时两个机位同步采图。双相机、Mono8、3 个采集轮次图像包约 72 MB，配置保留 `frame_slot_size=134217728`（128 MB）。如果后续增加光源或分辨率变更，应重新计算并同步 Python 共享内存配置。

当前产线固定为 `light_order=1,2,3`，Python 固定机位生产配方 `seat_a_black_leather_production_v1` 已同步为 `DIFFUSE/POLAR_DIFFUSE/HIGH_LEFT`。若未来补常亮 Dome ROI 或第 4 路 `HIGH_RIGHT`，必须同时更新 C++ 配置、Python 生产配方、模型输入通道、训练资产和测试。

Hikrobot MVS backend 当前按 `Mono8` 单通道实现，并已按海康 MVS C++ 示例工程对齐：进程内引用计数调用 `MV_CC_Initialize/Finalize`，枚举 GigE/USB/GenTL 设备，按 `camera.<N>.serial_number` 匹配相机，配置 `4096 x 3072`、`TriggerMode=On`、`Line1=ExposureStartActive`、`StrobeEnable=true`。C++ 每个采集轮次先设置曝光/增益并 arm 两台相机；频闪轮次使用 `TriggerSource=Line0`，再通过 RS232 发送 FL-ACDH 频闪序列（C→B→8→9→A→7），`light.response_mode=none` 时只校验串口写入；FL-ACDH 的 `7` 命令同时点亮频闪并通过同步输出触发相机 Line0 曝光。取帧使用 MVS 示例里的 `nExtendWidth/nExtendHeight/nFrameLenEx` 字段，其他像素格式不会隐式转换，配置不匹配会保守失败。

PLC 接入前，使用 `config/station_runtime.test.conf` 做手动触发联调，只验证相机、频闪、共享内存和 Python detector 收图。该配置的 `frame_slot_size=134217728` 会被联调脚本同步传给 Python detector，避免 4096 x 3072 图像在 Python 侧仍按默认 16 MB 打开共享内存。进入生产闭环前必须确认 `signal.backend=tcp_signal`、`signal.port`、结果回传地址和现场 PLC/TCP 信号协议。

配置字段逐项说明见 [C++ 主控部署与硬件运维](../docs/cpp_controller_operations.md)。

新增生产校验要点：

- `capture_schedule=shared_light_parallel` 仅支持 `capture_mode=fixed_camera`，且同一相机不能在多个视角中被同一次共享光源触发复用；机器人飞拍必须保持 pose 级串行。
- `capture_mode=fixed_camera` 时检测视角默认由 `camera.<N>` 自动生成；当前现场配置不使用 `pose.<N>.*`。
- 生产模式必须使用相机触发线、频闪控制器同步输出或等价硬触发同步；当前 FL-ACDH 方案为控制器 F 口输出到相机 `Line0`，相机 `Line1` ExposureStartActive 保留调试。
- `strobe_width_us` 不能大于 `exposure_us`，`frame_slot_size` 必须能容纳 `view_count x light_count` 的完整图像包。
- 相机 `pixel_format` 只接受当前已知格式，`Mono10/Mono12/Mono16/BayerRG12` 按 2 bytes/channel 估算 frame slot 容量，避免高位深图像因容量低估写爆 slot。
- `warning_recheck_threshold` 和 `critical_recheck_threshold` 控制连续复检报警升级，后者必须大于前者。
- `trace_root` 指定 C++ 生产事件日志目录，默认写入 `trace/cpp_controller_events.jsonl`。

---

## 数据流详解

### 一次完整检测周期

```
1. wait_for_trigger()
   └─ ISignalClient::wait_trigger()  → 生成 ExternalTrigger (模拟)
       trigger_id = auto-increment
       seat_id    = "SIM_SEAT_XXX"
       sku        = "seat_a_black_leather"

2. inspect_one_seat(trigger)
   │
   ├─ load_recipe(sku)           → Recipe (打光顺序)
   │
   ├─ frame_assembler_.acquire_bundles()
   │   ├─ build_capture_plan()                   (固定机位或机器人 pose 计划)
   │   ├─ wait_robot_pose_ready()                (固定机位为模拟 ready，机器人飞拍读取 READY/SHOT_ID)
   │   ├─ LightController::prepare_sequence()    (每个检测视角重新准备光源)
   │   ├─ 按 light_order 遍历每个光源通道:
   │   │   ├─ LightController::trigger_channel() 或 arm_hardware_trigger()
   │   │   ├─ CameraWorker::arm()                 (相机进入就绪)
   │   │   ├─ CameraDevice::simulate_exposure_output()  (模拟曝光输出)
   │   │   ├─ LightController::notify_hardware_triggered()
   │   │   └─ CameraWorker::wait_frame() → CameraDevice::capture()
   │   │       └─ 生成合成图像 (纹理 + 梯度) → CapturedFrame
   │   └─ 返回 SeatImageBundle { job_meta, frames[] }
   │
   ├─ frame_ring_.publish(bundle)
   │   └─ FrameRingBuffer 写入共享内存
   │       状态: Empty → Writing → Ready
   │
   ├─ result_ring_.wait_for_result(sequence_id)
   │   └─ ResultRingBuffer 轮询共享内存等待 Python 检测器结果
   │       状态: Ready → Reading → Empty
   │
   ├─ validate_detector_result()  (校验 sequence_id/trigger_id/CRC)
   │
   └─ signal_client_.publish_result()  → 发布外部信号结果 OK/NG/Recheck
```

### 错误处理策略

所有异常路径均返回 `Recheck` 决策（保守失败，宁可复检也不漏检）：

| 故障场景 | ErrorCode | 决策 |
|---------|-----------|------|
| 光源故障/arm 失败 | LightFault | Recheck |
| 相机丢帧 | MissingFrame | Recheck |
| 相机 arm 失败 | CameraFault | Recheck |
| 曝光输出或硬触发确认失败 | TriggerSyncFault | Recheck |
| 机器人未到位、FAULT 或 SHOT_ID 异常 | RobotFault | Recheck |
| 频闪配置缺失或非法 | ConfigurationError | Recheck |
| 槽位不可用 | SlotUnavailable | Recheck |
| 检测超时 | DetectorTimeout | Recheck |
| CRC 校验失败 | CrcMismatch | Recheck |
| 结果校验失败 | InvalidPayload | Recheck |
| 外部信号结果发布失败 | DeviceFault | Recheck |

同时，C++ 主控会把 `inspection_start`、`inspection_complete`、`inspection_recheck`、`signal_result_publish_failed` 等事件写入 `trace_root/cpp_controller_events.jsonl`。事件包含 `timestamp_us`、`sequence_id`、`trigger_id`、`seat_id`、`sku`、`decision`、`error_code` 和错误说明，用于现场复盘采集、IPC、detector 超时和外部信号结果发布故障。

`DetectorTimeout` 会把工位健康状态升级为 `Fault`；后续 `wait_for_trigger()` 会拒绝继续等待外部信号触发并记录 `trigger_wait_blocked_by_fault`，直到外部复位或重新初始化。这样 detector 失联不会让产线在未知检测能力下继续放行新座椅。

---

## 频闪时序控制

核心逻辑在 `src/control/frame_assembler.cpp` 的 `acquire_bundles()` 方法中。`capture_schedule=view_serial_tdm` 采用“逐检测视角串行、逐光源串行”；`capture_schedule=shared_light_parallel` 采用“逐共享光源串行、同光源下多固定机位同步采图”。

### 设计原则

```
固定机位模式下，检测视角等同于相机机位；机器人飞拍模式下，检测视角等同于机器人 pose。
共享光源硬件接线允许同一路光源频闪同时照亮两个固定机位时，可使用 shared_light_parallel 降低节拍：光源1触发一次，TOP_BACK 和 TOP_CUSHION 同时曝光；光源2/3 同理。
机器人飞拍和同一相机多 pose 场景不能使用共享光源并行，因为同一相机无法在同一光源触发中同时采多个 pose。
```

### 源码入口

`StationController::inspect_one_seat()` → `frame_assembler_.acquire_bundles(recipe, trigger, sequence_id, &bundle, &acquisition_error)`

### 执行步骤

**Step 1 — 构建光源序列**

```cpp
// frame_assembler.cpp
LightSequence sequence;
for (std::uint32_t light_index : recipe.light_order) {
    const auto& configured = find_light_channel_config(light_index);
    sequence.channels.push_back(LightChannelParam{
        configured.light_index,
        configured.physical_channel,
        configured.exposure_us,
        configured.strobe_width_us,
        configured.trigger_delay_us,
        configured.gain,
        configured.current_percent});
}
```

根据 Recipe 中的 `light_order`（如 `[1,2,3,4]`）从运行配置构建光源通道参数；缺少配置或参数非法会返回 `ConfigurationError`。

**Step 2 — 构建检测视角计划**

```cpp
std::vector<RuntimeCaptureViewConfig> capture_plan;
build_capture_plan(&capture_plan, error);
```

`capture_mode=fixed_camera` 且未显式配置 `pose.<N>` 时，会按 `camera.<N>` 自动生成视角；`capture_mode=robot_flyshot` 必须配置 `pose.<N>`，每个 pose 可以引用同一个末端相机或不同相机。

**Step 3 — 选择采集调度**

```cpp
if (config_.capture_schedule == CaptureSchedule::SharedLightParallel) {
    acquire_shared_light_parallel_frames(...);
} else {
    acquire_view_serial_tdm_frames(...);
}
```

**Step 4 — prepare_sequence**

```cpp
    light_controller_.prepare_sequence(sequence, trigger.trigger_id, ...);
```

**Step 5A — 视角串行 TDM**

```cpp
    for (std::uint32_t light_seq_index = 0;
         light_seq_index < sequence.channels.size();
         ++light_seq_index) {
        const auto light_param = sequence.channels[light_seq_index];
        // 当前光源频闪 → 当前视角相机拍摄 → 下一个光源
    }
}
```

**Step 5B — 共享光源并行**

```cpp
for (std::uint32_t light_seq_index = 0;
     light_seq_index < sequence.channels.size();
     ++light_seq_index) {
    for (const auto& view : capture_plan) {
        camera_for_index(view.camera_index)->arm(trigger_id, light_param, ...);
    }
    light_controller.trigger_channel(light_param, trigger_id, ...);
    for (const auto& view : capture_plan) {
        camera_for_index(view.camera_index)->wait_frame(...);
    }
}
```

### 频闪采集时序（对齐 Deploy）

每个光源步骤 = arm 相机 → 频闪完整序列 (C→B→8→9→A→7) → 取图 → post_delay：

```
C++ 程序 (单视角单光源, FL-ACDH 硬触发)
  │
  ├──①──→ camera.arm(光源N)
  │        └─ 设置相机曝光时间/增益
  │
  ├──②──→ light_controller_.trigger_channel(光源N)
  │        └─ FL-ACDH: C→B→8→9→A→7 完整序列
  │           C: 联动模式=0   B: 触发边沿=1   8: 触发模式=0
  │           9: 频闪脉宽     A: 相机延迟      7: 点火!
  │           └─ F口同步输出 → 相机 Line0 → 曝光采集
  │
  ├──③──→ camera.wait_frame()     ← 从 MVS SDK 取图
  │
  ├──④──→ sleep(post_delay_ms)    ← 光源间等待（默认 50ms）
  │
  └──⑤──→ 下一个光源 或下一个视角
```

### 完整时序图

```
假设: 2个检测视角 (A, B), light_order = [1, 2, 3]

time ────────────────────────────────────────────────────────────→

  ═══════════ 视角A ═══════════
    Cam A arm → Light 1 trigger (C→B→8→9→A→7) → Cam A 取图 → post_delay
    Cam A arm → Light 2 trigger → Cam A 取图 → post_delay
    Cam A arm → Light 3 trigger → Cam A 取图 → post_delay

  ═══════════ 视角B ═══════════
    Cam B arm → Light 1 trigger → Cam B 取图 → post_delay
    Cam B arm → Light 2 trigger → Cam B 取图 → post_delay
    Cam B arm → Light 3 trigger → Cam B 取图 → post_delay

最终产出: 2视角 × 3光源 = 6 张图像 → SeatImageBundle → FrameRingBuffer
```

生产固定机位共享光源并行：

```text
假设: 2个检测视角 (A, B), light_order = [1, 2, 3]

time ────────────────────────────────────────────────────────────→

  Light 1: Cam A arm + Cam B arm → Light 1 trigger → Cam A/B 取图 → post_delay
  Light 2: Cam A arm + Cam B arm → Light 2 trigger → Cam A/B 取图 → post_delay
  Light 3: Cam A arm + Cam B arm → Light 3 trigger → Cam A/B 取图 → post_delay

最终产出仍是: 2视角 × 3光源 = 6 张图像 → SeatImageBundle → FrameRingBuffer
发布给 Python 的帧顺序仍为“视角A全光源→视角B全光源”。
```

### 实际运行日志

```
[trigger_id=1000] prepared light sequence channels=3
[trigger_id=1000 light_index=1 physical_channel=1 light_seq_index=0] simulated strobe strobe_width_us=700 post_delay_ms=50
[trigger_id=1000 light_index=2 physical_channel=2 light_seq_index=1] simulated strobe strobe_width_us=700 post_delay_ms=50
[trigger_id=1000 light_index=3 physical_channel=3 light_seq_index=2] simulated strobe strobe_width_us=650 post_delay_ms=50
```

### 关键设计要点

| 特性 | 说明 |
|------|------|
| **可配置调度** | `view_serial_tdm` 保持旧的视角串行；`shared_light_parallel` 用于固定双机位共享光源同步采图 |
| **逐光源串行** | 无论哪种调度，一次只触发一个 `light_seq_index`，避免不同光源互相污染 |
| **同光源多机位采图** | `shared_light_parallel` 下同一路光源触发前 arm 所有固定机位相机，触发一次后分别收图 |
| **采集包完整性校验** | 发布共享内存前校验 `frame_count == view_count x light_count`，并确认帧顺序为“当前视角全光源→下一视角” |
| **每视角重新 prepare** | 切换视角时重新调用 `prepare_sequence()`，确保光源状态正确 |
| **Arm → 触发 → 确认** | 三阶段握手机制，模拟真实硬件的 GPIO 时序 |
| **各视角独立曝光输出** | 每个视角的相机自行发出曝光输出信号；机器人飞拍会同时记录 `shot_id`、`pose_id` 和 TCP 位姿 |
| **参数配置化** | `LightChannelParam` 来自运行配置，包含 `physical_channel`、`exposure_us`、`strobe_width_us`、`trigger_delay_us`、`gain` 和 `current_percent` |
| **结构化采集错误** | 采集失败返回 `AcquisitionError`，包含错误码、阶段、机位、光源和光源轮次 |
| **故障即停** | 任何一步失败立即 `shutdown_all()`，清空相机列表，下次调用重新初始化 |

---

## 独立部署到测试机

### 可行性

✅ **模拟模式可独立部署。**无需任何真实硬件即可在测试机运行共享内存和故障注入流程。

生产模式部署前必须完成：

1. 按 `config/station_runtime.production.conf` 核对现场外部信号、相机、频闪参数。
2. 运行 `--validate-config`，确保没有缺失点位或容量不足。
3. 按现场硬件型号链接 Hikrobot MVS SDK，并确认 FL-ACDH 串口号和 TCP 信号协议。
4. 做外部信号断线、相机缺帧、频闪故障、detector 超时等 fail-closed 验证。
5. 确认 `trace/cpp_controller_events.jsonl` 能按 `sequence_id` 和 `trigger_id` 记录复检原因。
6. 运行 `uv run python -m tools.run_cpp_soak --jobs 20 --wait-ms 8000` 做短时长稳压测，上线前按现场节拍扩大到 8h/24h。

### 部署步骤

```powershell
# 1. 复制项目到测试机
scp -r cpp_controller/ user@test-machine:/opt/seat-aoi/

# 2. 构建（仅需 CMake + C++17 编译器）
ssh user@test-machine
cd /opt/seat-aoi/cpp_controller
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build

# 3. 运行模拟链路
./build/seat_aoi_controller --loop
```

### 需要补充的 Mock 检测器

当前主循环在 `wait_for_result()` 处需要 Python detector 写回结果。联调时优先使用根目录跨平台入口：

```powershell
uv run python tools/run_simulated_ipc.py
```

---

## 设计原则

1. **默认零外部依赖** — 模拟模式使用纯 C++17 和系统共享内存 API，Linux/macOS/Windows 可按平台直接编译运行
2. **生产配置先行** — 先通过配置模板和 `--validate-config` 固化现场参数，再按 backend 接入真实驱动
3. **保守失败** — 任何异常路径均返回 Recheck，宁可复检不误判通过
4. **CRC 校验** — 所有共享内存数据帧附带 CRC32，防止静默数据损坏
5. **无锁环形缓冲区** — 使用 `std::atomic` CAS 操作，C++ 与 Python 侧无需额外同步原语
6. **故障注入支持** — 命令行和配置文件均支持故障注入，便于验证容错路径
7. **生产事件可追溯** — C++ 写出 JSONL 事件日志，现场可按 `sequence_id` 和 `trigger_id` 追踪 RECHECK 来源
