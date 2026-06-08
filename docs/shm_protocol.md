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
