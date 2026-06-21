# Seat Surface AOI - C++ Controller

`cpp_controller` 是当前产线主控程序。现阶段只保留一条真实需要的链路：接收外部信号，驱动 1 台 FL-ACDH 频闪控制器，2 个固定机位共享 3 路光源采图，并在在线模式下通过共享内存与 Python detector 交换图像和检测结果。

## 当前架构

```mermaid
flowchart LR
  Signal["外部信号\nmanual/external/tcp"] --> Station["StationController"]
  Station --> Assembler["FrameAssembler"]
  Assembler --> Strobe["FL-ACDH\nserial_ascii"]
  Strobe --> L1["光源 1"]
  Strobe --> L2["光源 2"]
  Strobe --> L3["光源 3"]
  Strobe --> Cam0["camera.0 TOP_BACK"]
  Strobe --> Cam1["camera.1 TOP_CUSHION"]
  Cam0 --> Assembler
  Cam1 --> Assembler
  Assembler --> Mode{"controller_mode"}
  Mode -->|online| FrameRing["FrameRingBuffer SHM"]
  FrameRing --> Py["Python detector"]
  Py --> ResultRing["ResultRingBuffer SHM"]
  ResultRing --> Station
  Mode -->|capture_only| Images["PGM 原图落盘"]
  Station --> Output["外部结果回传"]
```

固定约束：

- `capture_mode=fixed_camera`
- `capture_schedule=shared_light_parallel`
- `light_order=1,2,3`
- 只允许 2 个相机：`camera.0` 和 `camera.1`
- 只允许 1 台光源控制器：`light.backend=serial_ascii`
- 非模拟现场相机只保留 `camera.backend=hikrobot_mvs`
- 在线模式才启用共享内存；采图模式不创建 Frame/Result ring

C++ 主控只保留上述当前链路。非当前链路的兼容路径、未使用 backend 枚举和对应源码已移除；共享内存协议布局保持与 Python detector 二进制兼容，C++ 结构命名统一为固定机位视图语义。

## 文件结构

```text
cpp_controller/
├── CMakeLists.txt
├── config/
│   ├── station_runtime.production.conf     # 生产在线模式
│   ├── station_runtime.test.conf           # 工控机手动触发联调
│   └── station_runtime.capture_only.conf   # 采图模式，不启用共享内存
├── include/
│   ├── camera/                             # ICamera、模拟相机、Hikrobot MVS 适配声明
│   ├── common/                             # 错误码、协议结构基础类型、字符串/时间工具
│   ├── control/                            # StationController、FrameAssembler、信号、频闪、配置
│   └── ipc/                                # 共享内存、Frame/Result ring、CRC、协议布局
├── src/
│   ├── camera/                             # 模拟相机、Hikrobot MVS、相机 worker
│   ├── control/                            # 主控、采集编排、FL-ACDH、外部信号、配置、事件日志
│   ├── ipc/                                # Windows/POSIX 共享内存和 ring buffer
│   └── main.cpp
└── tools/
    ├── ipc_safety_checks.cpp               # C++ 侧安全回归
    └── protocol_layout.cpp                 # 协议结构大小输出
```

## 核心流程

在线模式 `controller_mode=online`：

1. 等待外部信号，生成 `ExternalTrigger`。
2. `FrameAssembler` 初始化 1 台 FL-ACDH 和 2 台相机。
3. 按光源顺序 1、2、3 逐路执行：先 arm 两台相机，再触发 FL-ACDH，再分别等待两台相机的帧。
4. 组包为 6 帧，发布到 `/seat_aoi_cpp_to_py_frames_v1`。
5. 等待 Python detector 写回 `/seat_aoi_py_to_cpp_results_v1`。
6. 校验 `sequence_id`、`trigger_id`、`seat_id`、CRC 和结果语义。
7. 通过外部信号回传 `OK`、`NG` 或 `RECHECK`。`ERROR` 会映射为外部 `RECHECK`。

采图模式 `controller_mode=capture_only`：

1. 仍然等待外部信号并完成相同的双机位三光源采集。
2. 不初始化共享内存，不发布 frame，不等待 Python detector。
3. 必须启用 `image_save.enabled=true` 和 `image_save.save_original=true`。
4. 原图保存为 `image_save.root_dir/YYYYMMDD/<seat_id>/<camera>_<timestamp>_L<light>_original.pgm`。
5. 完成后向外部信号回传 `RECHECK`，返回结果错误码为 `None`，表示这是主动旁路检测的采样任务。

## 配置说明

三份配置入口：

| 文件 | 模式 | 说明 |
| --- | --- | --- |
| `config/station_runtime.production.conf` | `online` | 生产 TCP 外部信号 + Hikrobot MVS + FL-ACDH + 共享内存检测。 |
| `config/station_runtime.test.conf` | `online` | 手动触发联调真实相机和频闪，仍走共享内存检测。 |
| `config/station_runtime.capture_only.conf` | `capture_only` | 手动触发采图，只保存 PGM 原图，不启用共享内存。 |

关键字段：

```ini
controller_mode=online          # online 或 capture_only
capture_mode=fixed_camera
capture_schedule=shared_light_parallel
light_order=1,2,3

signal.backend=tcp_signal       # production 常用；lab 可用 manual_trigger
camera.backend=hikrobot_mvs
light.backend=serial_ascii

camera.0.camera_id=TOP_BACK
camera.1.camera_id=TOP_CUSHION

light.serial_port=COM3
light.baud_rate=115200
light.response_mode=none
light.trigger_input_line=Line0

# 超时配置（毫秒）
camera_timeout_ms=800
light_timeout_ms=800
# arm 完成后到触发频闪前的相机稳定等待 (ms)，默认 5，0 则跳过
# arm_settle_ms=5

image_save.enabled=true
image_save.save_original=true
```

`hardware_mode=production` 禁止 simulated/manual backend；`hardware_mode=lab` 可用 `manual_trigger` 做手动联调；不传 `--config` 时仍保留内置 simulated fallback，用于本地 IPC 回归。

## 构建

```powershell
cmake -S cpp_controller -B cpp_controller/build/codex-check -DCMAKE_BUILD_TYPE=Release
cmake --build cpp_controller/build/codex-check --config Release
```

启用 Hikrobot MVS SDK 时显式传入 SDK 路径：

```powershell
cmake -S cpp_controller -B cpp_controller/build/hikrobot-release `
  -DCMAKE_BUILD_TYPE=Release `
  -DSEAT_AOI_ENABLE_HIKROBOT_MVS=ON `
  -DSEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR="C:/Program Files (x86)/MVS/Development/Includes" `
  -DSEAT_AOI_HIKROBOT_MVS_LIBRARY="C:/Program Files (x86)/MVS/Development/Libraries/win64/MvCameraControl.lib"
cmake --build cpp_controller/build/hikrobot-release --config Release
```

未启用 SDK 时，`camera.backend=hikrobot_mvs` 会在初始化阶段明确失败，不会回退模拟相机。

## 运行

```powershell
# 配置校验，不初始化硬件和共享内存
cpp_controller\build\codex-check\Release\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.production.conf --validate-config

# 单次在线检测
cpp_controller\build\codex-check\Release\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.test.conf --once

# 循环生产运行
cpp_controller\build\codex-check\Release\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.production.conf --loop

# 采图模式，只保存原图
cpp_controller\build\codex-check\Release\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.capture_only.conf --once

# 清理共享内存
cpp_controller\build\codex-check\Release\seat_aoi_controller.exe --cleanup
```

常用故障注入：

```powershell
--simulate-light-fault
--simulate-missing-frame
--simulate-signal-result-fault
--simulate-trigger-timeout
```

## 验证

```powershell
cmake --build cpp_controller/build/codex-check --config Release
cpp_controller\build\codex-check\Release\ipc_safety_checks.exe
uv run python -m tools.validate_protocol
uv run python tools/run_simulated_ipc.py
```

本次主控收敛后，`ipc_safety_checks` 覆盖了以下关键点：

- CRC/slot 状态错误必须 fail closed。
- 光源故障、缺帧、槽不可用、检测超时必须返回 `RECHECK`。
- `capture_only` 必须保存 6 张原图，且不能创建 Frame/Result 共享内存。
- 多频闪控制器、非 `1,2,3` 光序、单相机配置必须被拒绝。
- detector 返回语义非法时不能输出 `OK`。

## 安全规则

- 任意超时、缺帧、协议错误、CRC 错误、质量失败、配置错误都不能输出 `OK`。
- 采图模式不做检测，因此固定回传 `RECHECK`。
- Python 不控制 PLC、相机或频闪。
- C++ 不实现深度学习推理。
- 在线图像和结果交换只使用共享内存。
