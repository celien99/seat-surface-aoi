# C++ 主控当前逻辑梳理

本文记录当前已经在工控机调通的 `cpp_controller` 在线主链路。它是当前生产可运行逻辑的交接入口，目标是让现场配置、源码调用和故障闭环能一一对上。

## 当前结论

`cpp_controller` 当前只保留固定机位共享频闪链路：

- `capture_mode=fixed_camera`
- `capture_schedule=shared_light_parallel`
- `controller_mode=online` 或 `capture_only`
- 1 台 FL-ACDH-20048-4，`light.backend=serial_ascii`
- 2 台海康 MV-CH120-20GC，`camera.backend=hikrobot_mvs`
- 3 路共享频闪光源，`light_order=1,2,3`
- 在线图像和检测结果只通过共享内存交换，不通过 TCP 传图

当前工控机配置已经固化：

| 项目 | 当前值 |
| --- | --- |
| 相机 0 | `TOP_BACK`，SN `DA9184656`，4096 x 3072，Mono8 |
| 相机 1 | `TOP_CUSHION`，SN `DA9184665`，4096 x 3072，Mono8 |
| 频闪控制器 | FL-ACDH-20048-4，`COM1 / 9600 8N1` |
| 频闪触发 | `F1~F3` 短接成同步输出总线，并联到两台相机黄色 `Line0` |
| 相机调试输出 | `Line1 ExposureStartActive`，只用于示波器调试 |
| 光源参数 | 15ms 相机曝光，300/500/700us 三路频闪脉宽 |
| Python 光源语义 | `1 -> DIFFUSE`，`2 -> POLAR_DIFFUSE`，`3 -> HIGH_LEFT` |

当前 C++ 源码不支持生产配置中的 `ambient` 光源采集，也不支持 `light_order=12,1,2,3`。Python 生产配方里 `DOME` 语义暂映射到 `DIFFUSE`，ROI 定位复用第一路频闪图；未来如果补常亮 Dome ROI 采集，需要同步修改 C++ 配置解析、采集编排、共享内存映射、Python 配方和相关测试。

## 模块职责

| 模块 | 关键文件 | 职责 |
| --- | --- | --- |
| CLI 入口 | `cpp_controller/src/main.cpp` | 解析 `--config`、`--once/--loop`、故障注入和超时覆盖；保持薄入口。 |
| 运行配置 | `cpp_controller/src/control/station_runtime_config.cpp` | 解析 ini 风格配置，校验当前固定机位共享频闪链路约束。 |
| 工位状态机 | `cpp_controller/src/control/station_controller.cpp` | 初始化信号、共享内存、采集器；等待触发；执行一轮检测；校验 Python 结果；回传外部结果。 |
| 采集编排 | `cpp_controller/src/control/frame_assembler.cpp` | 初始化 FL-ACDH 和相机；构建光源序列与相机视角；按光源轮次并行采集所有机位。 |
| 海康相机 | `cpp_controller/src/camera/hikrobot_mvs_camera.cpp` | MVS SDK 初始化、硬触发配置、曝光/增益更新、`GetImageBuffer` 取帧和帧元数据填充。 |
| FL-ACDH 频闪 | `cpp_controller/src/control/fl_acdh_light_controller.cpp` | 串口打开、命令组帧、ACK 等待、`8/9/A/7` 完整点亮序列。 |
| 外部信号 | `cpp_controller/src/control/signal_client.cpp`、`cpp_controller/src/control/tcp_signal_client.cpp` | 手动/文件/TCP 触发输入，结果回传和健康状态。 |
| 共享内存 | `cpp_controller/src/ipc/frame_ring_buffer.cpp`、`cpp_controller/src/ipc/result_ring_buffer.cpp` | Frame/Result ring slot 状态机、CRC 和超时处理。 |
| 健康状态 | `cpp_controller/src/control/station_health.cpp` | 统计 OK/NG/RECHECK/ERROR、连续复检、设备故障和 detector timeout。 |

## 一次在线检测时序

```text
main.cpp
  -> load_station_runtime_config()
  -> StationController.initialize()
      -> FrameAssembler.configure()
      -> create_signal_client()
      -> FrameRingBuffer.initialize()
      -> ResultRingBuffer.initialize()
  -> StationController.wait_for_trigger()
      -> manual/external/tcp signal wait_trigger()
  -> StationController.inspect_one_seat()
      -> FrameAssembler.acquire_bundles()
          -> ensure_initialized()
              -> create_light_controller(serial_ascii)
              -> create_camera(hikrobot_mvs) x N
          -> build_light_sequence(light_order=1,2,3)
          -> build_capture_plan(camera.0, camera.1)
          -> prepare_sequence()
          -> 每路光源循环:
              -> 并行 arm 所有相机，仅更新 ExposureTime/Gain
              -> 等待 arm_settle_ms
              -> FL-ACDH trigger_channel(8/9/A/7)
              -> 并行 wait_frame(GetImageBuffer)
          -> 物理按光源采集，发布前重排为 view 优先
      -> 保存原图（如果 image_save.enabled）
      -> FrameRingBuffer.publish()
      -> ResultRingBuffer.wait_for_result()
      -> validate_detector_result()
      -> signal_client.publish_result()
      -> 记录事件和健康状态
```

关键点：

- 相机初始化时设置 `Continuous + TriggerMode On + TriggerSource=Line0 + RisingEdge`。
- `arm()` 阶段不重复写 `TriggerSource`，只更新曝光和增益，避免相机短暂错过硬触发沿。
- SDK 缓存在每轮频闪触发前并行 drain 所有相机，排空 arm() 改曝光参数可能在 Continuous 模式下即时产生的残留帧，避免误取为硬触发帧；相机启动或故障重启时也会排空旧帧。
- 物理采集顺序是“光源优先、相机并行”，共享内存发布顺序是“机位优先、光源顺序”，方便 Python 按视角组包。

## 在线模式与采图模式

`controller_mode=online`：

1. 初始化 Frame/Result 共享内存。
2. 采集 2 x 3 = 6 帧。
3. 发布到 `/seat_aoi_cpp_to_py_frames_v1`。
4. 等待 Python 写回 `/seat_aoi_py_to_cpp_results_v1`。
5. 校验结果并对外回传 `OK/NG/RECHECK`，Python `ERROR` 对外映射为 `RECHECK`。

`controller_mode=capture_only`：

1. 不初始化共享内存。
2. 执行同样的真实相机和频闪采集。
3. 原图保存到 `image_save.root_dir/YYYYMMDD/<seat_id>/`。
4. 对外固定回传 `RECHECK`，表示主动旁路检测的采样任务。

## 结果保守校验

C++ 接收 Python 结果后必须校验：

- `sequence_id` 必须等于本次发布的 sequence。
- `trigger_id` 必须等于本次外部触发。
- `seat_id` 必须等于本次外部触发。
- `decision` 只能是 `OK/NG/RECHECK/ERROR`。
- `defect_count` 必须和 payload 缺陷数量一致。
- `OK` 必须满足 `quality_pass=true`、`error_code=None`、`defect_count=0`。
- `NG` 必须满足 `quality_pass=true`、`error_code=None`、`defect_count>0`。
- `ERROR` 必须带非零 `error_code`，且对外回传降级为 `RECHECK`。

因此缺帧、超时、光源故障、相机故障、共享内存 slot 不可用、CRC 错误、Python detector timeout、质量门禁失败或非法结果都不会输出 `OK`。

## 故障恢复边界

当前实现有两层恢复：

- 单台相机连续失败达到 `max_camera_failures_before_reset` 后执行 `stop()+start()`。
- 采集链路连续失败达到阈值后释放并重建相机与频闪控制器。

这些恢复只用于下一轮重新尝试；当前失败任务仍然输出 `RECHECK` 或 `ERROR`，不能补判为 `OK`。

## 配置入口

| 配置 | 用途 |
| --- | --- |
| `cpp_controller/config/station_runtime.production.conf` | 生产在线模式：TCP 外部信号、Hikrobot MVS、FL-ACDH、共享内存、Python detector。 |
| `cpp_controller/config/station_runtime.test.conf` | 工控机联调模式：手动触发，真实相机和真实频闪，仍走共享内存。 |
| `cpp_controller/config/station_runtime.capture_only.conf` | 双相机采图模式：手动触发，真实相机和真实频闪，只保存原图。 |
| `cpp_controller/config/station_runtime.capture_only.single_camera.conf` | 单相机诊断采图：对齐外部成功程序的单相机、单光源链路。 |

## 现场维护注意事项

- 修改相机 SN、COM 口、光源通道、曝光、脉宽、slot 大小时，必须同步 C++ 运行配置、Python detector 启动配置和现场记录。
- 生产高分辨率双相机三光源至少使用当前 `frame_slot_size=134217728`。
- 未启用 Hikrobot MVS SDK 构建时，`camera.backend=hikrobot_mvs` 会初始化失败，不会回退模拟相机。
- `hardware_mode=production` 禁止 simulated/manual backend；工控机手动联调使用 `station_runtime.test.conf`。
- 当前 C++ 不实现深度学习推理；模型、ROI、特征、融合和规则判定仍属于 Python detector。
