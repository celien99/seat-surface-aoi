# C++ 主控生产上线 SOP

本文只覆盖 `cpp_controller` 生产上线流程。真实 PLC、机器人、相机和频闪驱动当前仍为空置占位；在未接入现场 SDK/协议适配器前，非 simulated backend 会 fail-fast，不能作为真实产线运行。

## 1. 上线前置条件

1. 固定机位方案复制并填写 `cpp_controller/config/station_runtime.production.example.conf`；机器人飞拍方案复制并填写 `cpp_controller/config/station_runtime.robot_flyshot.production.example.conf`。
2. C++ 主控固定视角级串行 TDM 采集，现场接线确认不会同时触发多个视角光源。
3. `trigger_sync_mode=camera_exposure_output` 或等价硬触发同步。
4. `strobe_width_us <= exposure_us`，电流、脉宽和触发延时不超过频闪控制器规格。
5. `frame_slot_size` 能容纳 `view_count x light_count` 的完整图像包。
6. Python detector 常驻运行，C++ 与 Python 协议校验通过。
7. 机器人飞拍方案必须冻结 `pose_id`、SHOT_ID、READY/FAULT/PHOTO_TRIGGER 点位、TCP 位姿和对应 Python 标定/配方。

## 2. 配置验收

```bash
cmake -S cpp_controller -B cpp_controller/build
cmake --build cpp_controller/build
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.production.conf \
  --validate-config
```

机器人飞拍方案：

```bash
cpp_controller/build/seat_aoi_controller \
  --config cpp_controller/config/station_runtime.robot_flyshot.production.conf \
  --validate-config
```

验收标准：

- 配置校验输出 `C++ station runtime config OK`。
- 配置中没有 `TODO`、空 PLC 点位、空相机序列号或 simulated backend。
- `warning_recheck_threshold < critical_recheck_threshold`。

## 3. 模拟链路验收

```bash
uv run python -m tools.validate_protocol
bash tools/run_simulated_ipc.sh
bash tools/run_simulated_ipc.sh --config cpp_controller/config/station_runtime.robot_flyshot.example.conf
```

验收标准：

- 协议结构体大小与 Python 侧一致。
- 固定机位和机器人飞拍模拟 IPC 返回 `OK`。
- 故障注入路径返回 `RECHECK` 或 `ERROR`，不能返回 `OK`。

## 4. 健康报警验收

C++ 主控会记录 `trace_root/cpp_controller_events.jsonl`。每条事件包含：

- `station_state`
- `alarm_level`
- `total_jobs`
- `ok_count`
- `recheck_count`
- `detector_timeout_count`
- `device_fault_count`
- `consecutive_recheck_count`

验收标准：

- 连续复检达到 `warning_recheck_threshold` 后进入 `Warning`。
- 连续复检达到 `critical_recheck_threshold` 后进入 `Fault/Critical`。
- PLC 输出失败、设备故障和 detector 超时均能在事件日志中定位到 `sequence_id` 和 `trigger_id`。

## 5. 长稳压测

短测：

```bash
bash tools/run_cpp_soak.sh --jobs 20 --wait-ms 8000
```

上线前建议：

```bash
bash tools/run_cpp_soak.sh --jobs 1000 --wait-ms 8000 \
  --trace-root trace/cpp_soak_8h
```

验收标准：

- `failed_iterations=0`。
- `trace_root/summary.txt` 存在。
- `cpp_controller_events.jsonl` 中无未解释的 `Critical` 报警。
- 不出现共享内存 slot 长期占用、detector timeout 累积或连续复检失控。

## 6. 真实驱动接入验收

真实驱动接入后，必须逐项复测：

1. PLC 触发去重、trigger_id 递增、seat_id/sku 读取正确。
2. OK/NG/RECHECK 输出点位和 PLC ack/复位逻辑正确。
3. 相机序列号与 `camera_index` 对应现场物理相机；固定机位再对应现场机位，机器人飞拍再通过 `pose_id` 对应轨迹视角。
4. 机器人飞拍方案中 `pose_id`、SHOT_ID、READY/FAULT/PHOTO_TRIGGER 与轨迹和 Python 配方一致。
5. 当前检测视角完成全部光源后才切换下一视角。
6. 单次频闪只触发当前视角光源，不污染其它视角。
7. 任一相机缺帧、频闪故障、机器人未到位/FAULT、PLC 断线都返回 `RECHECK` 或 `ERROR`。
8. 真实 8h/24h 长稳压测通过。

## 7. 放行规则

满足以下条件才允许上线：

- 配置验收通过。
- 模拟链路验收通过。
- 健康报警验收通过。
- 真实驱动验收通过。
- 长稳压测通过。
- Python detector 真实模型、配方、标定和追溯链路同步验收通过。
