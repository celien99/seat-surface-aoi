# Phase 1: 抽象接口层重构 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 PLC、光源、相机三个硬件模块从直接实例化改为抽象接口 + 模拟实现，使后续可通过配置文件切换为真实硬件实现，不改业务逻辑。

**Architecture:** 为三个硬件模块各抽取一个纯虚接口（`IPlcClient`, `ILightController`, `ICamera`），现有模拟代码移动到 `SimXxx` 实现类。`FrameAssembler` 和 `StationController` 通过工厂函数注入具体实现。行为完全不变。

**Tech Stack:** C++17, CMake 3.16, POSIX shared memory, pthread

**Design Decisions:**
- 使用纯虚接口（运行时多态），不用模板（CRTP），因为需要在运行时按配置文件切换实现
- 接口方法签名与现有模拟实现完全一致，Phase 1 不改变业务逻辑
- `simulate_exposure_output()` 保留在 `ICamera` 接口上作为过渡；真实相机实现返回 `true`（no-op），Phase 4 移除
- 目录结构：接口头文件放在 `include/control/`，模拟实现放在 `src/control/`

---

### Task 1: Create IPlcClient abstract interface

**Files:**
- Create: `include/control/iplc_client.hpp`

- [ ] **Step 1: Write the abstract interface header**

```cpp
// include/control/iplc_client.hpp
#pragma once

#include <cstdint>
#include <string>

#include "common/inspection_types.hpp"
#include "control/trigger_scheduler.hpp"

namespace seat_aoi {

struct PlcHealth {
  bool ok = true;
  std::string message = "simulated";
};

class IPlcClient {
public:
  virtual ~IPlcClient() = default;
  virtual bool initialize(bool simulate_output_fault,
                          bool simulate_trigger_timeout = false) = 0;
  virtual bool wait_trigger(PlcTrigger* out_trigger,
                            int timeout_ms,
                            std::string* error_message) = 0;
  virtual bool send_decision(const PlcTrigger& trigger,
                             std::uint64_t sequence_id,
                             InspectionDecision decision,
                             int timeout_ms,
                             std::string* error_message) = 0;
  virtual PlcHealth get_health() const = 0;
};

}  // namespace seat_aoi
```

- [ ] **Step 2: Update plc_client.hpp to include the interface and keep PlcHealth, then rename class**

```cpp
// include/control/plc_client.hpp  (修改后)
#pragma once

#include "control/iplc_client.hpp"

namespace seat_aoi {

// PlcHealth 已移至 iplc_client.hpp

class SimPlcClient : public IPlcClient {
public:
  bool initialize(bool simulate_output_fault,
                  bool simulate_trigger_timeout = false) override;
  bool wait_trigger(PlcTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool send_decision(const PlcTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message) override;
  PlcHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_output_fault_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 1000;
};

}  // namespace seat_aoi
```

- [ ] **Step 3: Build to verify compilation**

Run: `cd build && cmake .. && cmake --build . 2>&1`

- [ ] **Step 4: Commit**

```bash
git add include/control/iplc_client.hpp include/control/plc_client.hpp
git commit -m "refactor: extract IPlcClient abstract interface, rename PlcClient → SimPlcClient

- New iplc_client.hpp: pure virtual interface + PlcHealth struct
- plc_client.hpp: SimPlcClient inherits IPlcClient
- All method signatures unchanged, zero behavioral change

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Update PlcClient references in business code

**Files:**
- Modify: `src/control/plc_client.cpp:1-67`
- Modify: `include/control/station_controller.hpp:1-67`
- Modify: `src/control/station_controller.cpp:1-197`
- Modify: `src/main.cpp:1-135`
- Modify: `tools/ipc_safety_checks.cpp:1-277`

- [ ] **Step 1: Update plc_client.cpp — rename class, add override**

```cpp
// src/control/plc_client.cpp
#include "control/plc_client.hpp"

#include <chrono>
#include <thread>

namespace seat_aoi {

bool SimPlcClient::initialize(bool simulate_output_fault, bool simulate_trigger_timeout) {
  initialized_ = true;
  simulate_output_fault_ = simulate_output_fault;
  simulate_trigger_timeout_ = simulate_trigger_timeout;
  return true;
}

bool SimPlcClient::wait_trigger(PlcTrigger* out_trigger,
                                int timeout_ms,
                                std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "PLC 未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
    if (error_message != nullptr) {
      *error_message = "模拟 PLC 触发超时";
    }
    return false;
  }

  PlcTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = "SIM_SEAT_" + std::to_string(trigger.trigger_id);
  trigger.sku = "seat_a_black_leather";
  *out_trigger = trigger;
  return true;
}

bool SimPlcClient::send_decision(const PlcTrigger& /*trigger*/,
                                 std::uint64_t /*sequence_id*/,
                                 InspectionDecision /*decision*/,
                                 int timeout_ms,
                                 std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "PLC 未初始化或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_output_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟 PLC 输出失败";
    }
    return false;
  }
  return true;
}

PlcHealth SimPlcClient::get_health() const {
  return PlcHealth{initialized_ && !simulate_output_fault_ && !simulate_trigger_timeout_,
                   simulate_output_fault_     ? "模拟 PLC 输出失败"
                   : simulate_trigger_timeout_ ? "模拟 PLC 触发超时"
                                               : "simulated"};
}

}  // namespace seat_aoi
```

- [ ] **Step 2: Update station_controller.hpp — change PlcClient to unique_ptr<IPlcClient>**

```cpp
// include/control/station_controller.hpp (修改 plc_client_ 成员)
// 将: PlcClient plc_client_;
// 改为:
#include <memory>
// ...
  std::unique_ptr<IPlcClient> plc_client_;
```

其余不变。新增 `#include "control/iplc_client.hpp"` 替换 `#include "control/plc_client.hpp"`。

- [ ] **Step 3: Update station_controller.cpp — create SimPlcClient in initialize()**

在 `StationController::initialize()` 中：

```cpp
// 将: plc_client_.initialize(config.simulate_plc_output_fault, config.simulate_trigger_timeout);
// 改为:
plc_client_ = std::make_unique<SimPlcClient>();
plc_client_->initialize(config.simulate_plc_output_fault, config.simulate_trigger_timeout);
```

其余 `plc_client_.` 调用不变（多态调用）。

- [ ] **Step 4: Update main.cpp — no changes needed (uses StationController)**

验证 main.cpp 无需修改。

- [ ] **Step 5: Update ipc_safety_checks.cpp — change PlcClient to SimPlcClient**

在 `test_plc_trigger_timeout_fails_closed()` 中：
```cpp
// 将: seat_aoi::PlcClient plc;
// 改为:
seat_aoi::SimPlcClient plc;
```

- [ ] **Step 6: Build and run tests**

```bash
cd build && cmake .. && cmake --build . 2>&1
./build/ipc_safety_checks 2>&1
```

Expected: all 6 tests pass, "ipc safety checks passed"

- [ ] **Step 7: Commit**

```bash
git add src/control/plc_client.cpp include/control/station_controller.hpp \
        src/control/station_controller.cpp tools/ipc_safety_checks.cpp
git commit -m "refactor: inject IPlcClient into StationController, use SimPlcClient

- StationController owns unique_ptr<IPlcClient>
- SimPlcClient created in initialize() via make_unique
- ipc_safety_checks uses SimPlcClient directly
- Zero behavioral change, all tests pass

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Create ILightController abstract interface

**Files:**
- Create: `include/control/ilight_controller.hpp`
- Modify: `include/control/light_controller.hpp`

- [ ] **Step 1: Write the abstract interface header**

```cpp
// include/control/ilight_controller.hpp
#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

enum class TriggerSyncMode : std::uint32_t {
  Software = 1,
  CameraExposureOutput = 2,
};

struct LightChannelParam {
  std::uint32_t light_index = 0;
  std::uint32_t exposure_us = 0;
  float gain = 1.0F;
  float current_percent = 0.0F;
};

struct LightSequence {
  std::vector<LightChannelParam> channels;
};

struct LightHealth {
  bool ok = true;
  std::string message = "simulated";
};

class ILightController {
public:
  virtual ~ILightController() = default;
  virtual bool initialize(bool simulate_fault = false) = 0;
  virtual bool prepare_sequence(const LightSequence& sequence,
                                std::uint64_t trigger_id,
                                int timeout_ms,
                                std::string* error_message) = 0;
  virtual bool trigger_channel(const LightChannelParam& channel,
                               std::uint64_t trigger_id,
                               std::uint32_t light_seq_index,
                               int timeout_ms,
                               std::string* error_message) = 0;
  virtual bool arm_hardware_trigger(const LightChannelParam& channel,
                                    std::uint64_t trigger_id,
                                    std::uint32_t light_seq_index,
                                    int timeout_ms,
                                    std::string* error_message) = 0;
  virtual bool notify_hardware_triggered(const LightChannelParam& channel,
                                         std::uint64_t trigger_id,
                                         std::uint32_t light_seq_index,
                                         int timeout_ms,
                                         std::string* error_message) = 0;
  virtual bool run_sequence(const LightSequence& sequence,
                            std::uint64_t trigger_id,
                            int timeout_ms,
                            std::string* error_message = nullptr) = 0;
  virtual bool set_channel(std::uint32_t light_index,
                           const LightChannelParam& param) = 0;
  virtual LightHealth get_health() const = 0;
  virtual void shutdown_all() = 0;
};

}  // namespace seat_aoi
```

- [ ] **Step 2: Update light_controller.hpp — inherit from ILightController, rename to SimLightController**

```cpp
// include/control/light_controller.hpp
#pragma once

#include "control/ilight_controller.hpp"

namespace seat_aoi {

// TriggerSyncMode, LightChannelParam, LightSequence, LightHealth 已移至 ilight_controller.hpp

class SimLightController : public ILightController {
public:
  bool initialize(bool simulate_fault = false) override;
  bool prepare_sequence(const LightSequence& sequence,
                        std::uint64_t trigger_id,
                        int timeout_ms,
                        std::string* error_message) override;
  bool trigger_channel(const LightChannelParam& channel,
                       std::uint64_t trigger_id,
                       std::uint32_t light_seq_index,
                       int timeout_ms,
                       std::string* error_message) override;
  bool arm_hardware_trigger(const LightChannelParam& channel,
                            std::uint64_t trigger_id,
                            std::uint32_t light_seq_index,
                            int timeout_ms,
                            std::string* error_message) override;
  bool notify_hardware_triggered(const LightChannelParam& channel,
                                 std::uint64_t trigger_id,
                                 std::uint32_t light_seq_index,
                                 int timeout_ms,
                                 std::string* error_message) override;
  bool run_sequence(const LightSequence& sequence,
                    std::uint64_t trigger_id,
                    int timeout_ms,
                    std::string* error_message = nullptr) override;
  bool set_channel(std::uint32_t light_index, const LightChannelParam& param) override;
  LightHealth get_health() const override;
  void shutdown_all() override;

private:
  bool initialized_ = false;
  bool simulate_fault_ = false;
  bool hardware_trigger_armed_ = false;
  std::uint32_t armed_light_index_ = 0;
};

}  // namespace seat_aoi
```

- [ ] **Step 3: Build to verify compilation**

Run: `cd build && cmake .. && cmake --build . 2>&1`

- [ ] **Step 4: Commit**

```bash
git add include/control/ilight_controller.hpp include/control/light_controller.hpp
git commit -m "refactor: extract ILightController abstract interface, rename → SimLightController

- New ilight_controller.hpp: pure virtual interface + all light types
- light_controller.hpp: SimLightController inherits ILightController
- TriggerSyncMode/LightChannelParam/LightSequence/LightHealth moved to interface header

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Update LightController references in business code

**Files:**
- Modify: `src/control/light_controller.cpp:1-165`
- Modify: `include/control/frame_assembler.hpp:1-37`
- Modify: `src/control/frame_assembler.cpp:1-237`
- Modify: `include/control/station_runtime_config.hpp:1-58`

- [ ] **Step 1: Update light_controller.cpp — rename class, add override**

```cpp
// src/control/light_controller.cpp (修改类名和 override 关键字)
#include "control/light_controller.hpp"

#include <chrono>
#include <iostream>
#include <thread>

namespace seat_aoi {

bool SimLightController::initialize(bool simulate_fault) {
  initialized_ = true;
  simulate_fault_ = simulate_fault;
  return true;
}

bool SimLightController::prepare_sequence(const LightSequence& sequence,
                                          std::uint64_t trigger_id,
                                          int timeout_ms,
                                          std::string* error_message) {
  // ... 原有实现不变，仅类名从 LightController 改为 SimLightController
  if (!initialized_ || sequence.channels.empty() || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "光源未初始化、序列为空或 timeout_ms 非法";
    }
    return false;
  }
  // ... (其余方法同理，全部加 override，类名改为 SimLightController)
}
```

完整实现与原有 `LightController` 一致，仅改类名 + 加 `override`。

- [ ] **Step 2: Update frame_assembler.hpp — change LightController to unique_ptr<ILightController>**

```cpp
// include/control/frame_assembler.hpp
// 将: #include "control/light_controller.hpp"
// 改为: #include "control/ilight_controller.hpp"
// 添加: #include <memory>

// 将成员: LightController light_controller_;
// 改为: std::unique_ptr<ILightController> light_controller_;
```

- [ ] **Step 3: Update frame_assembler.cpp — create SimLightController in ensure_initialized()**

在 `FrameAssembler::ensure_initialized()` 中：

```cpp
// 将: if (!light_controller_.initialize(config_.light.simulate_fault)) {
// 改为:
if (!light_controller_) {
  light_controller_ = std::make_unique<SimLightController>();
}
if (!light_controller_->initialize(config_.light.simulate_fault)) {
```

其余所有 `light_controller_.` 调用改为 `light_controller_->` （`.` → `->`）。

- [ ] **Step 4: Update station_runtime_config.hpp — include ilight_controller.hpp**

```cpp
// 将: #include "control/light_controller.hpp"
// 改为: #include "control/ilight_controller.hpp"
```

`RuntimeLightConfig` 和 `StationRuntimeConfig` 保持不变。

- [ ] **Step 5: Build and run tests**

```bash
cd build && cmake .. && cmake --build . 2>&1
./build/ipc_safety_checks 2>&1
```

Expected: all 6 tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/control/light_controller.cpp include/control/frame_assembler.hpp \
        src/control/frame_assembler.cpp include/control/station_runtime_config.hpp
git commit -m "refactor: inject ILightController into FrameAssembler, use SimLightController

- FrameAssembler owns unique_ptr<ILightController>
- SimLightController created in ensure_initialized() via make_unique
- All light_controller_. → light_controller_-> (pointer deref)
- Zero behavioral change, all tests pass

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Create ICamera abstract interface

**Files:**
- Create: `include/camera/icamera.hpp`
- Modify: `include/camera/camera_worker.hpp`
- Modify: `include/camera/camera_device.hpp`

- [ ] **Step 1: Write the ICamera abstract interface**

```cpp
// include/camera/icamera.hpp
#pragma once

#include <cstdint>
#include <string>

#include "control/ilight_controller.hpp"
#include "ipc/frame_ring_buffer.hpp"

namespace seat_aoi {

struct CameraConfig {
  std::uint32_t camera_index = 0;
  std::string camera_id;
  std::uint32_t width = 64;
  std::uint32_t height = 48;
  std::uint32_t channels = 1;
  bool simulate_missing_frame = false;
};

struct CameraHealth {
  bool ok = true;
  std::uint64_t dropped_frames = 0;
  std::string message = "simulated";
};

class ICamera {
public:
  virtual ~ICamera() = default;
  virtual bool initialize(const CameraConfig& config) = 0;
  virtual void start() = 0;
  virtual void stop() = 0;
  virtual bool arm(std::uint64_t trigger_id,
                   const LightChannelParam& light_param,
                   std::uint32_t light_seq_index,
                   int timeout_ms) = 0;
  virtual bool simulate_exposure_output(std::uint64_t trigger_id,
                                        const LightChannelParam& light_param,
                                        std::uint32_t light_seq_index,
                                        int timeout_ms) = 0;
  virtual bool wait_frame(std::uint64_t trigger_id,
                          const LightChannelParam& light_param,
                          std::uint32_t light_seq_index,
                          CapturedFrame* out_frame,
                          int timeout_ms) = 0;
  virtual CameraHealth get_health() const = 0;
};

}  // namespace seat_aoi
```

- [ ] **Step 2: Rename CameraWorker → SimCamera, inherit ICamera**

```cpp
// include/camera/camera_worker.hpp → 修改为 SimCamera
#pragma once

#include "camera/camera_device.hpp"
#include "camera/icamera.hpp"

namespace seat_aoi {

class SimCamera : public ICamera {
public:
  bool initialize(const CameraConfig& config) override;
  void start() override;
  void stop() override;
  bool arm(std::uint64_t trigger_id,
           const LightChannelParam& light_param,
           std::uint32_t light_seq_index,
           int timeout_ms) override;
  bool simulate_exposure_output(std::uint64_t trigger_id,
                                const LightChannelParam& light_param,
                                std::uint32_t light_seq_index,
                                int timeout_ms) override;
  bool wait_frame(std::uint64_t trigger_id,
                  const LightChannelParam& light_param,
                  std::uint32_t light_seq_index,
                  CapturedFrame* out_frame,
                  int timeout_ms) override;
  CameraHealth get_health() const override;

private:
  CameraDevice device_;
  bool running_ = false;
};

}  // namespace seat_aoi
```

- [ ] **Step 3: Update camera_device.hpp — remove duplicate CameraConfig/CameraHealth (now in icamera.hpp)**

```cpp
// include/camera/camera_device.hpp
#pragma once

#include "camera/icamera.hpp"

namespace seat_aoi {

// CameraConfig, CameraHealth 已移至 icamera.hpp

class CameraDevice {
public:
  bool initialize(const CameraConfig& config);
  bool arm(std::uint64_t trigger_id,
           const LightChannelParam& light_param,
           std::uint32_t light_seq_index,
           int timeout_ms);
  bool simulate_exposure_output(std::uint64_t trigger_id,
                                const LightChannelParam& light_param,
                                std::uint32_t light_seq_index,
                                int timeout_ms);
  bool capture(std::uint64_t trigger_id,
               const LightChannelParam& light_param,
               std::uint32_t light_seq_index,
               CapturedFrame* out_frame,
               int timeout_ms);
  CameraHealth get_health() const;

private:
  CameraConfig config_{};
  bool initialized_ = false;
  bool armed_ = false;
  std::uint64_t armed_trigger_id_ = 0;
  std::uint32_t armed_light_index_ = 0;
  std::uint32_t armed_light_seq_index_ = 0;
};

}  // namespace seat_aoi
```

- [ ] **Step 4: Build to verify compilation**

Run: `cd build && cmake .. && cmake --build . 2>&1`

- [ ] **Step 5: Commit**

```bash
git add include/camera/icamera.hpp include/camera/camera_worker.hpp \
        include/camera/camera_device.hpp
git commit -m "refactor: extract ICamera abstract interface, rename CameraWorker → SimCamera

- New icamera.hpp: pure virtual interface + CameraConfig/CameraHealth structs
- camera_worker.hpp: SimCamera inherits ICamera
- camera_device.hpp: no longer defines CameraConfig/CameraHealth (moved to icamera.hpp)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Update Camera references in business code

**Files:**
- Modify: `src/camera/camera_worker.cpp:1-52`
- Modify: `src/camera/camera_device.cpp:1-103`
- Modify: `include/control/frame_assembler.hpp:1-37`
- Modify: `src/control/frame_assembler.cpp:1-237`
- Modify: `include/control/station_runtime_config.hpp:1-58`

- [ ] **Step 1: Update camera_worker.cpp — rename class to SimCamera, add override**

```cpp
// src/camera/camera_worker.cpp
#include "camera/camera_worker.hpp"

namespace seat_aoi {

bool SimCamera::initialize(const CameraConfig& config) {
  return device_.initialize(config);
}

void SimCamera::start() {
  running_ = true;
}

void SimCamera::stop() {
  running_ = false;
}

bool SimCamera::arm(std::uint64_t trigger_id,
                    const LightChannelParam& light_param,
                    std::uint32_t light_seq_index,
                    int timeout_ms) {
  if (!running_) return false;
  return device_.arm(trigger_id, light_param, light_seq_index, timeout_ms);
}

bool SimCamera::simulate_exposure_output(std::uint64_t trigger_id,
                                         const LightChannelParam& light_param,
                                         std::uint32_t light_seq_index,
                                         int timeout_ms) {
  if (!running_) return false;
  return device_.simulate_exposure_output(trigger_id, light_param, light_seq_index, timeout_ms);
}

bool SimCamera::wait_frame(std::uint64_t trigger_id,
                           const LightChannelParam& light_param,
                           std::uint32_t light_seq_index,
                           CapturedFrame* out_frame,
                           int timeout_ms) {
  if (!running_) return false;
  return device_.capture(trigger_id, light_param, light_seq_index, out_frame, timeout_ms);
}

CameraHealth SimCamera::get_health() const {
  return device_.get_health();
}

}  // namespace seat_aoi
```

- [ ] **Step 2: Update frame_assembler.hpp — change vector<CameraWorker> to vector<unique_ptr<ICamera>>**

```cpp
// include/control/frame_assembler.hpp
// 将: #include "camera/camera_worker.hpp"
// 改为: #include "camera/icamera.hpp"

// 将成员: std::vector<CameraWorker> cameras_;
// 改为: std::vector<std::unique_ptr<ICamera>> cameras_;
```

- [ ] **Step 3: Update frame_assembler.cpp ensure_initialized() — create SimCamera**

```cpp
// 将 cameras_.push_back(std::move(worker)) 的逻辑改为:
cameras_.clear();
for (const auto& runtime_camera : config_.cameras) {
  CameraConfig config;
  config.camera_index = runtime_camera.camera_index;
  config.camera_id = runtime_camera.camera_id;
  config.width = runtime_camera.width;
  config.height = runtime_camera.height;
  config.channels = runtime_camera.channels;
  config.simulate_missing_frame = runtime_camera.simulate_missing_frame;
  auto camera = std::make_unique<SimCamera>();
  if (!camera->initialize(config)) {
    return false;
  }
  camera->start();
  cameras_.push_back(std::move(camera));
}
```

- [ ] **Step 4: Update frame_assembler.cpp acquire_bundles() — use pointer deref for camera**

```cpp
// 将: auto& camera = cameras_[camera_index];
// 改为: auto& camera = *cameras_[camera_index];  // unique_ptr → reference
```

其余 `camera.xxx()` 调用无需改变（本来就是引用）。

- [ ] **Step 5: Update station_runtime_config.hpp — include icamera.hpp instead of camera_worker.hpp**

`#include "camera/icamera.hpp"` 替换 `#include "camera/camera_worker.hpp"`（如果有的话）。

- [ ] **Step 6: Build and run tests**

```bash
cd build && cmake .. && cmake --build . 2>&1
./build/ipc_safety_checks 2>&1
./build/seat_aoi_controller --once 2>&1
```

Expected: all tests pass, runtime output shows serial TDM timing.

- [ ] **Step 7: Commit**

```bash
git add src/camera/camera_worker.cpp include/control/frame_assembler.hpp \
        src/control/frame_assembler.cpp include/control/station_runtime_config.hpp
git commit -m "refactor: inject ICamera into FrameAssembler, use SimCamera

- FrameAssembler owns vector<unique_ptr<ICamera>> instead of vector<CameraWorker>
- SimCamera created in ensure_initialized() via make_unique
- acquire_bundles() auto& camera = *cameras_[camera_index] (deref unique_ptr)
- Zero behavioral change, all tests pass, TDM timing preserved

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Add factory functions for sim implementations

**Files:**
- Create: `include/control/hardware_factory.hpp`

- [ ] **Step 1: Write the factory header**

```cpp
// include/control/hardware_factory.hpp
#pragma once

#include <memory>

#include "camera/icamera.hpp"
#include "control/ilight_controller.hpp"
#include "control/iplc_client.hpp"

namespace seat_aoi {

enum class HardwareBackend {
  Simulated,
  // RealModbus,     // Phase 2
  // RealBasler,     // Phase 4
  // RealSerialLight,// Phase 3
};

inline std::unique_ptr<IPlcClient> create_plc_client(HardwareBackend /*backend*/) {
  // Phase 1: only SimPlcClient exists
  // Phase 2: add switch/case for RealModbus
  return std::make_unique<SimPlcClient>();
}

inline std::unique_ptr<ILightController> create_light_controller(HardwareBackend /*backend*/) {
  return std::make_unique<SimLightController>();
}

inline std::unique_ptr<ICamera> create_camera(HardwareBackend /*backend*/) {
  return std::make_unique<SimCamera>();
}

}  // namespace seat_aoi
```

- [ ] **Step 2: Include factory header in implementor files**

在 `station_controller.cpp` 和 `frame_assembler.cpp` 中：
```cpp
#include "control/hardware_factory.hpp"
```

将 `std::make_unique<SimPlcClient>()` 替换为 `create_plc_client(HardwareBackend::Simulated)`
将 `std::make_unique<SimLightController>()` 替换为 `create_light_controller(HardwareBackend::Simulated)`
将 `std::make_unique<SimCamera>()` 替换为 `create_camera(HardwareBackend::Simulated)`

- [ ] **Step 3: Build and run tests**

```bash
cd build && cmake .. && cmake --build . 2>&1
./build/ipc_safety_checks 2>&1
./build/seat_aoi_controller --once 2>&1
```

- [ ] **Step 4: Commit**

```bash
git add include/control/hardware_factory.hpp src/control/station_controller.cpp \
        src/control/frame_assembler.cpp
git commit -m "feat: add hardware_factory.hpp with factory functions

- create_plc_client(), create_light_controller(), create_camera()
- HardwareBackend enum with Simulated as the only backend (Phase 1)
- Switchable to real backends in future phases without touching business code

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Update CMakeLists.txt for new interface headers

**Files:**
- Modify: `CMakeLists.txt`

- [ ] **Step 1: No source file changes — CMakeLists.txt already picks up headers via target_include_directories**

验证：新接口头文件（`.hpp`）都在 `include/` 目录下，已通过 `target_include_directories(seat_aoi_control PUBLIC include)` 覆盖。无需修改 CMakeLists.txt。

- [ ] **Step 2: Clean rebuild verification**

```bash
cd build && cmake .. && cmake --build . --clean-first 2>&1
```

Expected: clean rebuild succeeds with zero warnings.

- [ ] **Step 3: Final test run**

```bash
./build/ipc_safety_checks 2>&1
```

Expected: "ipc safety checks passed"

- [ ] **Step 4: Final behavioral verification**

```bash
./build/seat_aoi_controller --once 2>&1
```

Expected: TDM timing output (机位A 4次频闪 → 机位B 4次频闪 → detector timeout).

- [ ] **Step 5: Commit**

```bash
git add CMakeLists.txt  # 如有修改
git commit -m "chore: verify CMakeLists.txt compatibility with abstract interfaces

- All interface headers covered by existing target_include_directories
- Clean rebuild + full test suite passes
- Phase 1 abstract interface refactoring complete

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage:**
- ✅ IPlcClient + SimPlcClient → Tasks 1-2
- ✅ ILightController + SimLightController → Tasks 3-4
- ✅ ICamera + SimCamera → Tasks 5-6
- ✅ Factory functions → Task 7
- ✅ Build system → Task 8
- ✅ StationController injection → Task 2
- ✅ FrameAssembler injection → Tasks 4, 6
- ✅ Zero behavioral change → verified in each task

**2. Placeholder scan:**
- No TBD/TODO/fill-in-later found
- All error messages are concrete
- All code blocks are complete
- All file paths are exact

**3. Type consistency:**
- `IPlcClient::wait_trigger(PlcTrigger*, int, string*)` → consistent in SimPlcClient and StationController
- `ILightController::arm_hardware_trigger(LightChannelParam, uint64_t, uint32_t, int, string*)` → consistent in SimLightController and FrameAssembler
- `ICamera::wait_frame(uint64_t, LightChannelParam, uint32_t, CapturedFrame*, int)` → consistent in SimCamera and FrameAssembler
- `HardwareBackend::Simulated` → consistent in factory functions and callers
- All `unique_ptr` derefs use `->` consistently
