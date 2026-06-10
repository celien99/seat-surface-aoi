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

## 版本与布局

第一版实现使用：

- `SHM_PROTOCOL_MAGIC = 0x53414F49`
- `SHM_PROTOCOL_VERSION = 1`
- 小端字段
- 固定大小 IPC 结构体
- 对 payload 和稳定 header 字段做 CRC32 校验

header CRC 特意排除了可变的 `state` 和 `header_crc32` 字段。原因是 slot 会从 `READY` 切到 `READING`，但 payload 仍然有效。

## 首次集成路径

1. 构建 C++ 主控。
2. 启动 `seat_aoi_controller`；它会发布一个模拟的多机位、多光源任务。
3. 启动 `python -m python_detector.detector_main --once`；它会读取任务、运行 fake 流水线并写回结果。
4. C++ 按 `sequence_id` 读取结果并打印最终判定。
