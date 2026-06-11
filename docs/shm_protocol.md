# 共享内存协议

在线图像和结果链路使用两个 POSIX 共享内存区域：

- `/seat_aoi_cpp_to_py_frames_v1`：C++ 写入 `SeatInspectionJob` 图像 slot，Python 读取。
- `/seat_aoi_py_to_cpp_results_v1`：Python 写入 `InspectionResult` 结果 slot，C++ 读取。

在线检测数据面禁止使用 TCP。

## Slot 状态机

图像 slot 的状态流转如下：

```text
EMPTY -> WRITING -> READY -> READING -> EMPTY
```

只有 C++ 主控可以写入图像 slot。只有 Python 检测进程可以读取并释放图像 slot。结果 slot 使用同一状态机，但方向相反。

C++ 主控不得覆盖 `READY` 或 `READING` 状态的 slot。检测超时、缺帧、CRC 不一致或协议不匹配必须输出 `RECHECK` 或 `ERROR`，不能输出 `OK`。

## 错误码

结果中的 `error_code` 是跨 C++ 与 Python 共享的枚举值。当前布局未改变，只扩展了枚举语义：

| 值 | 名称 | 含义 |
| ---: | --- | --- |
| 0 | `None` | 无错误。 |
| 1 | `ProtocolMismatch` | 协议 magic、version 或布局不匹配。 |
| 2 | `InvalidPayload` | payload 边界、字段或结果校验失败。 |
| 3 | `CrcMismatch` | header 或 payload CRC 校验失败。 |
| 4 | `SlotUnavailable` | 共享内存 slot 在超时前不可用。 |
| 5 | `DetectorTimeout` | C++ 等待 Python 检测结果超时。 |
| 6 | `MissingFrame` | 相机缺帧或等待图像超时。 |
| 7 | `QualityFailed` | Python 图像质量门禁失败。 |
| 8 | `DeviceFault` | PLC 输出或通用设备故障。 |
| 9 | `InternalError` | 进程内部异常。 |
| 10 | `LightFault` | 频闪配置、prepare、软件触发或 arm 失败。 |
| 11 | `CameraFault` | 相机 arm 或相机设备状态失败。 |
| 12 | `TriggerSyncFault` | 曝光输出、硬触发确认或触发同步失败。 |
| 13 | `ConfigurationError` | C++ 运行配置缺失、非法或不支持。 |
| 14 | `RobotFault` | 机器人未到位、SHOT_ID/位置触发异常或机器人 FAULT。 |

## 版本与布局

当前第二版实现使用：

- `SHM_PROTOCOL_MAGIC = 0x53414F49`
- `SHM_PROTOCOL_VERSION = 2`
- 小端字段
- 固定大小 IPC 结构体
- 对 payload 和稳定 header 字段做 CRC32 校验

header CRC 特意排除了可变的 `state` 和 `header_crc32` 字段。原因是 slot 会从 `READY` 切到 `READING`，但 payload 仍然有效。

## V2 视角与机器人飞拍字段

V2 协议把原先的“机位包”扩展为“检测视角包”：

- 固定机位多光源：`pose_id == camera_id`，每个固定相机就是一个检测视角。
- 机器人飞拍多光源：同一末端相机可以在多个 `pose_id` 下采图，例如 `T1_BACKREST`、`T2_CUSHION`。

`SeatJobMeta` 中的 `view_count` 表示本次任务包含的检测视角数量，`capture_mode` 表示固定机位或机器人飞拍模式。`LightFrameMeta` 每帧都携带：

- `camera_index` / `camera_id`
- `pose_index` / `pose_id`
- `light_index` / `light_seq_index`
- `shot_id`
- `robot_timestamp_us`
- `robot_tcp_xyz_mm[3]`
- `robot_rpy_deg[3]`
- `calibration_id`

Python detector 按 `(camera_id, pose_id)` 组包为 `CameraBundle`，质量门禁、预处理、ROI、配准、特征、融合和结果 trace 都保留 `pose_id`。缺少关键 pose、机器人未到位、触发错序、缺帧或 CRC 错误必须返回 `RECHECK` 或 `ERROR`。

## 结构体大小

当前协议布局：

| 结构体 | 大小 |
| --- | ---: |
| `ShmHeader` | 40 |
| `FrameSlotHeader` | 268 |
| `ResultSlotHeader` | 140 |
| `LightFrameMeta` | 324 |
| `SeatJobMeta` | 232 |
| `InspectionResultMeta` | 104 |
| `DefectResultMeta` | 464 |

## 首次集成路径

1. 构建 C++ 主控。
2. 启动 `seat_aoi_controller`；它会发布一个模拟的多机位、多光源任务。
3. 启动 `python -m python_detector.detector_main --once`；它会读取任务、运行 fake 流水线并写回结果。
4. C++ 按 `sequence_id` 读取结果并打印最终判定。
