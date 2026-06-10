# Seat Surface AOI — C++ Controller

## 概述

`cpp_controller` 是座椅表面 AOI 检测系统的 C++ 主控程序，负责工位流程编排、图像采集调度、PLC 信号交互，以及通过 POSIX 共享内存与 Python 检测引擎进行 IPC 通信。

### 核心定位

```
┌──────────────────────────────────────────────────────────────────┐
│                     C++ Controller (本工程)                       │
│                                                                  │
│   PLC 触发 ─→ 光源时序 ─→ 相机采图 ─→ FrameRingBuffer (SHM)       │
│                                          │                       │
│                                          ↓                       │
│                                   [Python 检测器]                 │
│                                          │                       │
│                                          ↓                       │
│   PLC 输出 ←─────────────── ResultRingBuffer (SHM)               │
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
│   │   ├── shared_memory.cpp               # POSIX shm_open/mmap 封装
│   │   ├── crc32.cpp                       # CRC32 校验和计算
│   │   ├── frame_ring_buffer.cpp           # 图像帧环形缓冲区（C++ → Python）
│   │   └── result_ring_buffer.cpp          # 检测结果环形缓冲区（Python → C++）
│   │
│   ├── control/                            # 工位控制逻辑层
│   │   ├── plc_client.cpp                  # PLC 客户端（模拟实现）
│   │   ├── light_controller.cpp            # 光源控制器（模拟实现）
│   │   ├── station_controller.cpp          # 工位主控协调器（核心编排）
│   │   ├── frame_assembler.cpp             # 多相机图像采集编排器
│   │   ├── trigger_scheduler.cpp           # 触发信号调度器
│   │   └── station_runtime_config.cpp      # 运行时配置文件解析
│   │
│   └── camera/                             # 相机模拟层
│       ├── camera_device.cpp               # 模拟相机设备（生成合成图像）
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
│       ├── plc_client.hpp                  # PlcClient + PlcHealth
│       ├── light_controller.hpp            # LightController + 光源类型定义
│       ├── frame_assembler.hpp             # FrameAssembler + Recipe
│       ├── trigger_scheduler.hpp           # TriggerScheduler + PlcTrigger
│       ├── station_runtime_config.hpp      # StationRuntimeConfig + 配置解析
│       └── camera/                         # 相机相关头文件
│           ├── camera_device.hpp           # CameraDevice + CameraConfig/CameraHealth
│           └── camera_worker.hpp           # CameraWorker
│
├── tools/
│   ├── protocol_layout.cpp                 # 打印所有协议结构体大小的诊断工具
│   └── ipc_safety_checks.cpp               # IPC 故障注入与安全测试套件
│
└── config/
    └── station_runtime.example.conf        # 运行时配置模板
```

---

## 编译产物

| Target | 类型 | 说明 |
|--------|------|------|
| `seat_aoi_ipc` | 静态库 | 共享内存、环形缓冲区、CRC32 |
| `seat_aoi_control` | 静态库 | PLC、光源、相机、工位编排 |
| `seat_aoi_controller` | 可执行文件 | 主程序入口 |
| `protocol_layout` | 可执行文件（工具） | 打印结构体大小，用于跨语言对齐校验 |
| `ipc_safety_checks` | 可执行文件（测试） | IPC 故障注入与安全测试 |

### 依赖关系

```
seat_aoi_ipc (无外部依赖，仅 pthread)
       ↑
seat_aoi_control (依赖 seat_aoi_ipc)
       ↑
seat_aoi_controller (依赖 seat_aoi_control)
ipc_safety_checks   (依赖 seat_aoi_control)
```

默认构建外部依赖为零，仅依赖 C++17 标准库 + POSIX（`shm_open`, `mmap`, `pthread`）。真实生产 backend 需要按现场硬件型号额外链接 PLC、相机或频闪厂商 SDK。

---

## 硬件模式说明

当前支持两种运行模式：

| 模式 | 配置 | 用途 |
|------|------|------|
| 模拟模式 | `hardware_mode=simulated` | 不需要真实硬件，使用模拟 PLC、模拟相机、模拟频闪跑通端到端 IPC 和故障注入 |
| 生产模式 | `hardware_mode=production` | 强制填写 PLC、相机、频闪现场参数，禁止误用 simulated backend；当前仓库提供配置校验和 fail-fast 保护，真实 SDK 需按型号接入 |

模拟模式行为：

| 模块 | 模拟行为 | 故障注入 |
|------|---------|---------|
| **PLC** (`plc_client.cpp`) | 自动生成虚拟触发信号（`SIM_SEAT_XXX`），递增 trigger_id | `--simulate-plc-output-fault` 模拟输出失败<br>`--simulate-trigger-timeout` 模拟触发超时 |
| **相机** (`camera_device.cpp`) | 生成合成图像（纹理 + 梯度 + 伪随机数据），64×48 可配置分辨率 | `--simulate-missing-frame` 模拟丢帧 |
| **光源** (`light_controller.cpp`) | 模拟频闪时序，1ms sleep 模拟硬件延迟 | `--simulate-light-fault` 模拟光源故障 |

生产模式当前支持的配置 backend 名称：

| 设备 | 可选 backend |
|------|--------------|
| PLC | `modbus_tcp`、`siemens_s7`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` |
| 相机 | `basler_pylon`、`hikrobot_mvs`、`daheng_galaxy`、`flir_spinnaker`、`vendor_sdk`、`custom_sdk` |
| 频闪 | `serial_ascii`、`modbus_tcp`、`ethercat_io`、`digital_io`、`vendor_sdk`、`custom_sdk` |

如果生产模式选择了非 simulated backend，但 C++ 尚未链接对应真实驱动，程序会在初始化阶段明确报错并退出，不会偷偷回退到模拟硬件。

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
│   version: 1                                        │
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
| `FrameSlotHeader` | 260 bytes | 帧槽位头（含 SeatJobMeta） |
| `ResultSlotHeader` | 140 bytes | 结果槽位头（含 InspectionResultMeta） |
| `LightFrameMeta` | 152 bytes | 单帧图像元数据 |
| `SeatJobMeta` | 224 bytes | 作业元数据 |
| `InspectionResultMeta` | 104 bytes | 检测结果元数据 |
| `DefectResultMeta` | 336 bytes | 单个缺陷描述 |

---

## 构建与运行

### 系统要求

- CMake ≥ 3.16
- C++17 编译器（GCC ≥ 8 / Clang ≥ 7）
- POSIX 兼容系统（Linux / macOS）

### 构建

```bash
cd cpp_controller
cmake -B build -DCMAKE_BUILD_TYPE=Release
cmake --build build
```

### 运行

```bash
# 单次检测（默认 --once 模式）
./build/seat_aoi_controller

# 循环模式（持续等待触发）
./build/seat_aoi_controller --loop

# 使用配置文件
./build/seat_aoi_controller --config config/station_runtime.example.conf

# 只校验生产配置，不启动 PLC/相机/频闪
./build/seat_aoi_controller --config config/station_runtime.production.conf --validate-config

# 故障注入测试
./build/seat_aoi_controller --simulate-light-fault
./build/seat_aoi_controller --simulate-missing-frame
./build/seat_aoi_controller --simulate-plc-output-fault
./build/seat_aoi_controller --simulate-trigger-timeout

# 自定义参数
./build/seat_aoi_controller --max-jobs 10 --wait-ms 3000

# 清理共享内存后退出
./build/seat_aoi_controller --cleanup

# 运行诊断工具
./build/protocol_layout

# 运行 IPC 安全测试
./build/ipc_safety_checks
```

### 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--config <path>` | 运行时配置文件路径 | 无 |
| `--loop` | 持续循环模式 | 单次 |
| `--once` | 单次执行（覆盖 --loop） | 默认 |
| `--no-reset` | 不重置共享内存 | false |
| `--cleanup` | 仅清理共享内存后退出 | - |
| `--validate-config` | 只校验运行配置后退出，不初始化共享内存和硬件 | false |
| `--max-jobs <N>` | 最大检测任务数（0=不限） | 0 |
| `--wait-ms <N>` | 检测结果等待超时(ms) | 5000 |
| `--trigger-timeout-ms <N>` | PLC 触发等待超时(ms) | 1000 |
| `--simulate-light-fault` | 模拟光源故障 | false |
| `--simulate-missing-frame` | 模拟相机丢帧 | false |
| `--simulate-plc-output-fault` | 模拟 PLC 输出失败 | false |
| `--simulate-trigger-timeout` | 模拟 PLC 触发超时 | false |

### 运行时配置文件格式

```ini
# key=value，支持 # 注释
hardware_mode=simulated
plc.backend=simulated
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
max_jobs=1
recipe_id=seat_a_black_leather_v1
acquisition_strategy=serial_tdm
light_order=1,2,3,4
trigger_sync_mode=camera_exposure_output
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
simulate_plc_output_fault=false
simulate_trigger_timeout=false
```

生产配置模板位于 `config/station_runtime.production.example.conf`。复制后替换所有 `TODO_*`：

```bash
cp config/station_runtime.production.example.conf config/station_runtime.production.conf
./build/seat_aoi_controller --config config/station_runtime.production.conf --validate-config
```

配置字段逐项说明见 [C++ 主控生产配置快速上手](../docs/cpp_controller_production_config_quickstart.md)。

新增生产校验要点：

- `acquisition_strategy` 当前只允许 `serial_tdm`，表示“当前机位全光源采集完成后再切换下一机位”。
- 生产模式必须使用 `camera_exposure_output` 或等价硬触发同步，`software` 仅用于模拟或低精度联调。
- `strobe_width_us` 不能大于 `exposure_us`，`frame_slot_size` 必须能容纳 `camera_count x light_count` 的完整图像包。
- `trace_root` 指定 C++ 生产事件日志目录，默认写入 `trace/cpp_controller_events.jsonl`。

---

## 数据流详解

### 一次完整检测周期

```
1. wait_for_trigger()
   └─ PlcClient::wait_trigger()  → 生成 PlcTrigger (模拟)
       trigger_id = auto-increment
       seat_id    = "SIM_SEAT_XXX"
       sku        = "seat_a_black_leather"

2. inspect_one_seat(trigger)
   │
   ├─ load_recipe(sku)           → Recipe (打光顺序)
   │
   ├─ frame_assembler_.acquire_bundles()
   │   ├─ LightController::prepare_sequence()    (光源准备)
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
   └─ plc_client_.send_decision()  → 模拟 PLC 输出 OK/NG/Recheck
```

### 错误处理策略

所有异常路径均返回 `Recheck` 决策（保守失败，宁可复检也不漏检）：

| 故障场景 | ErrorCode | 决策 |
|---------|-----------|------|
| 光源故障/arm 失败 | LightFault | Recheck |
| 相机丢帧 | MissingFrame | Recheck |
| 相机 arm 失败 | CameraFault | Recheck |
| 曝光输出或硬触发确认失败 | TriggerSyncFault | Recheck |
| 频闪配置缺失或非法 | ConfigurationError | Recheck |
| 槽位不可用 | SlotUnavailable | Recheck |
| 检测超时 | DetectorTimeout | Recheck |
| CRC 校验失败 | CrcMismatch | Recheck |
| 结果校验失败 | InvalidPayload | Recheck |
| PLC 输出失败 | DeviceFault | Recheck |

同时，C++ 主控会把 `inspection_start`、`inspection_complete`、`inspection_recheck`、`plc_output_failed` 等事件写入 `trace_root/cpp_controller_events.jsonl`。事件包含 `timestamp_us`、`sequence_id`、`trigger_id`、`seat_id`、`sku`、`decision`、`error_code` 和错误说明，用于现场复盘采集、IPC、detector 超时和 PLC 输出故障。

---

## 频闪时序控制

核心逻辑在 `src/control/frame_assembler.cpp` 的 `acquire_bundles()` 方法中，采用 **时分频闪（TDM）方案："逐机位串行、逐光源串行"**。

### 设计原则

```
实际产线约束：每个机位独立完成全部光源频闪序列，机位之间串行执行。
即：机位A 依次完成 [光源1→光源2→光源3→光源4] 全部拍摄后，机位B 才开始。
这是硬规则，不是性能优化选项；多机位并行频闪会造成光源互相污染，当前配置和采集包校验都会拒绝偏离 `serial_tdm` 的路径。
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

**Step 2 — 外层循环：逐机位串行**

```cpp
for (std::uint32_t camera_index = 0; camera_index < cameras_.size(); ++camera_index) {
    auto& camera = cameras_[camera_index];
    // 当前机位完成全部光源序列后，才进入下一个机位
```

**Step 3 — 每个机位重新 prepare_sequence**

```cpp
    light_controller_.prepare_sequence(sequence, trigger.trigger_id, ...);
```

**Step 4 — 内层循环：逐光源串行**

```cpp
    for (std::uint32_t light_seq_index = 0;
         light_seq_index < sequence.channels.size();
         ++light_seq_index) {
        const auto light_param = sequence.channels[light_seq_index];
        // 当前光源频闪 → 当前机位相机拍摄 → 下一个光源
    }
}
```

### 两种触发同步模式

项目支持两种光源触发模式，由 `trigger_sync_mode` 配置：

#### 模式 A：Software（软件触发）

```
C++ 程序
  │
  ├──①──→ light_controller_.trigger_channel(光源N)
  │        └─ 模拟频闪脉冲 (simulated strobe, sleep 1ms)
  │
  ├──②──→ camera.wait_frame()    ← 单相机串行采集
  │        └─ CameraDevice::capture() → 生成合成图像 (sleep 2ms)
  │
  └──③──→ 下一个光源 (light_seq_index++) 或下一个机位 (camera_index++)
```

#### 模式 B：CameraExposureOutput（相机曝光输出硬触发，默认模式）

模拟真实硬件的 GPIO 联动——相机开始曝光时通过 Strobe Out 引脚发出脉冲信号，触发光源频闪：

```
C++ 程序 (单机位单光源)
  │
  ├──①──→ light_controller_.arm_hardware_trigger(光源N)
  │        └─ 光源进入"预就绪"状态，等待曝光输出信号
  │
  ├──②──→ camera.arm(光源N)      ← 仅当前机位相机 arm
  │        └─ CameraDevice 记录 armed_trigger_id / armed_light_index
  │
  ├──③──→ camera.simulate_exposure_output(光源N)
  │        └─ 当前机位相机模拟曝光输出信号 (sleep 1ms)
  │        └─ 真实场景：相机开始曝光 → GPIO Strobe Out → 光源频闪
  │
  ├──④──→ light_controller_.notify_hardware_triggered(光源N)
  │        └─ 光源确认已收到硬件触发，执行频闪 (sleep 1ms)
  │
  ├──⑤──→ camera.wait_frame(光源N)   ← 单相机串行采集
  │        └─ CameraDevice::capture() → 生成合成图像 (sleep 2ms)
  │
  └──⑥──→ 下一个光源 或 下一个机位
```

### 完整时序图（时分频闪）

```
假设: 2台相机 (A, B), light_order = [1, 2, 3, 4], 模式 = CameraExposureOutput

time ────────────────────────────────────────────────────────────────────────→

  ═══════════ 机位A (Camera 0) ═══════════
  prepare_sequence ─→
    Light 1 arm ─→ Cam A arm ─→ Cam A 曝光输出 ─→ Light 1 频闪 ─→ Cam A 采图
    Light 2 arm ─→ Cam A arm ─→ Cam A 曝光输出 ─→ Light 2 频闪 ─→ Cam A 采图
    Light 3 arm ─→ Cam A arm ─→ Cam A 曝光输出 ─→ Light 3 频闪 ─→ Cam A 采图
    Light 4 arm ─→ Cam A arm ─→ Cam A 曝光输出 ─→ Light 4 频闪 ─→ Cam A 采图

  ═══════════ 机位B (Camera 1) ═══════════
  prepare_sequence ─→
    Light 1 arm ─→ Cam B arm ─→ Cam B 曝光输出 ─→ Light 1 频闪 ─→ Cam B 采图
    Light 2 arm ─→ Cam B arm ─→ Cam B 曝光输出 ─→ Light 2 频闪 ─→ Cam B 采图
    Light 3 arm ─→ Cam B arm ─→ Cam B 曝光输出 ─→ Light 3 频闪 ─→ Cam B 采图
    Light 4 arm ─→ Cam B arm ─→ Cam B 曝光输出 ─→ Light 4 频闪 ─→ Cam B 采图

最终产出: 2机位 × 4光源 = 8 张图像 → SeatImageBundle → FrameRingBuffer
```

### 实际运行日志

```
[trigger_id=1000] prepared light sequence channels=4         ← 机位A 开始
[trigger_id=1000 light_index=1 physical_channel=1 light_seq_index=0] arm ...    ← 光源1
[trigger_id=1000 light_index=1 physical_channel=1 light_seq_index=0] camera exposure output fired strobe
[trigger_id=1000 light_index=2 physical_channel=2 light_seq_index=1] arm ...    ← 光源2
[trigger_id=1000 light_index=2 physical_channel=2 light_seq_index=1] camera exposure output fired strobe
[trigger_id=1000 light_index=3 physical_channel=3 light_seq_index=2] arm ...    ← 光源3
[trigger_id=1000 light_index=3 physical_channel=3 light_seq_index=2] camera exposure output fired strobe
[trigger_id=1000 light_index=4 physical_channel=4 light_seq_index=3] arm ...    ← 光源4
[trigger_id=1000 light_index=4 physical_channel=4 light_seq_index=3] camera exposure output fired strobe
[trigger_id=1000] prepared light sequence channels=4         ← 机位B 开始
[trigger_id=1000 light_index=1 physical_channel=1 light_seq_index=0] arm ...    ← 光源1
...
```

### 关键设计要点

| 特性 | 说明 |
|------|------|
| **逐机位串行** | 外层循环按 camera_index 串行，每个机位独立完成全部光源序列后再切换 |
| **逐光源串行** | 内层循环按 light_seq_index 串行，一次只有一个光源频闪 |
| **单相机采集** | 每次频闪仅当前机位的相机拍摄，不再使用 `std::async` 并行 |
| **采集包完整性校验** | 发布共享内存前校验 `frame_count == camera_count x light_count`，并确认帧顺序为“当前机位全光源→下一机位” |
| **每机位重新 prepare** | 切换机位时重新调用 `prepare_sequence()`，确保光源状态正确 |
| **Arm → 触发 → 确认** | 三阶段握手机制，模拟真实硬件的 GPIO 时序 |
| **各机位独立曝光输出** | 每个机位的相机自行发出曝光输出信号，不再仅有相机0 负责 |
| **参数配置化** | `LightChannelParam` 来自运行配置，包含 `physical_channel`、`exposure_us`、`strobe_width_us`、`trigger_delay_us`、`gain` 和 `current_percent` |
| **结构化采集错误** | 采集失败返回 `AcquisitionError`，包含错误码、阶段、机位、光源和光源轮次 |
| **故障即停** | 任何一步失败立即 `shutdown_all()`，清空相机列表，下次调用重新初始化 |

---

## 独立部署到测试机

### 可行性

✅ **模拟模式可独立部署。**无需任何真实硬件即可在测试机运行共享内存和故障注入流程。

生产模式部署前必须完成：

1. 按 `config/station_runtime.production.example.conf` 填写现场 PLC、相机、频闪参数。
2. 运行 `--validate-config`，确保没有 `TODO` 占位和缺失点位。
3. 按现场硬件型号链接真实 PLC、相机、频闪 SDK 或协议适配器。
4. 做 PLC 断线、相机缺帧、频闪故障、detector 超时等 fail-closed 验证。
5. 确认 `trace/cpp_controller_events.jsonl` 能按 `sequence_id` 和 `trigger_id` 记录复检原因。

### 部署步骤

```bash
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

当前主循环在 `wait_for_result()` 处会超时（没有 Python 检测器写结果），需要补充一个 Mock Result Writer：

```python
# mock_detector.py — 模拟检测器，向 ResultRingBuffer 写入结果
import mmap
import struct
import os
import time

SHM_NAME = "/seat_aoi_py_to_cpp_results_v1"
# ... 打开共享内存，轮询 Ready 状态的槽位，写入模拟检测结果
```

---

## 设计原则

1. **默认零外部依赖** — 模拟模式使用纯 C++17 + POSIX，任何 Linux/macOS 系统可直接编译运行
2. **生产配置先行** — 先通过配置模板和 `--validate-config` 固化现场参数，再按 backend 接入真实驱动
3. **保守失败** — 任何异常路径均返回 Recheck，宁可复检不误判通过
4. **CRC 校验** — 所有共享内存数据帧附带 CRC32，防止静默数据损坏
5. **无锁环形缓冲区** — 使用 `std::atomic` CAS 操作，C++ 与 Python 侧无需额外同步原语
6. **故障注入支持** — 命令行和配置文件均支持故障注入，便于验证容错路径
7. **生产事件可追溯** — C++ 写出 JSONL 事件日志，现场可按 `sequence_id` 和 `trigger_id` 追踪 RECHECK 来源
