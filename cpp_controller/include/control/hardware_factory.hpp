#pragma once

#include <memory>
#include <string>

#include "camera/camera_worker.hpp"
#include "camera/hikrobot_mvs_camera.hpp"
#include "control/hardware_backend.hpp"
#include "control/fl_acdh_light_controller.hpp"
#include "control/light_controller.hpp"
#include "control/signal_client.hpp"
#include "control/tcp_signal_client.hpp"
#include "control/robot_client.hpp"

namespace seat_aoi {

namespace detail {

inline std::string unsupported_driver_message(const char* device,
                                              HardwareBackend backend) {
  return std::string(device) + " backend=" + hardware_backend_name(backend) +
         " 尚未链接真实硬件驱动。请按 docs/cpp_controller_operations.md "
         "填写现场参数，并在 C++ 中接入对应厂商 SDK/协议适配器。";
}

class UnsupportedSignalClient final : public ISignalClient {
public:
  explicit UnsupportedSignalClient(HardwareBackend backend) : backend_(backend) {}

  bool initialize(const SignalClientConfig& /*config*/) override {
    return false;
  }

  bool wait_trigger(ExternalTrigger* /*out_trigger*/,
                    int /*timeout_ms*/,
                    std::string* error_message) override {
    if (error_message != nullptr) {
      *error_message = unsupported_driver_message("ExternalSignal", backend_);
    }
    return false;
  }

  bool publish_result(const ExternalTrigger& /*trigger*/,
                     std::uint64_t /*sequence_id*/,
                     InspectionDecision /*decision*/,
                     int /*timeout_ms*/,
                     std::string* error_message) override {
    if (error_message != nullptr) {
      *error_message = unsupported_driver_message("ExternalSignal", backend_);
    }
    return false;
  }

  SignalHealth get_health() const override {
    return SignalHealth{false, unsupported_driver_message("ExternalSignal", backend_)};
  }

private:
  HardwareBackend backend_;
};

class UnsupportedLightController final : public ILightController {
public:
  explicit UnsupportedLightController(HardwareBackend backend) : backend_(backend) {}

  bool initialize(const LightControllerConfig& /*config*/) override {
    return false;
  }

  bool prepare_sequence(const LightSequence& /*sequence*/,
                        std::uint64_t /*trigger_id*/,
                        int /*timeout_ms*/,
                        std::string* error_message) override {
    set_error(error_message);
    return false;
  }

  bool trigger_channel(const LightChannelParam& /*channel*/,
                       std::uint64_t /*trigger_id*/,
                       std::uint32_t /*light_seq_index*/,
                       int /*timeout_ms*/,
                       std::string* error_message) override {
    set_error(error_message);
    return false;
  }

  bool arm_hardware_trigger(const LightChannelParam& /*channel*/,
                            std::uint64_t /*trigger_id*/,
                            std::uint32_t /*light_seq_index*/,
                            int /*timeout_ms*/,
                            std::string* error_message) override {
    set_error(error_message);
    return false;
  }

  bool notify_hardware_triggered(const LightChannelParam& /*channel*/,
                                 std::uint64_t /*trigger_id*/,
                                 std::uint32_t /*light_seq_index*/,
                                 int /*timeout_ms*/,
                                 std::string* error_message) override {
    set_error(error_message);
    return false;
  }

  bool run_sequence(const LightSequence& /*sequence*/,
                    std::uint64_t /*trigger_id*/,
                    int /*timeout_ms*/,
                    std::string* error_message = nullptr) override {
    set_error(error_message);
    return false;
  }

  bool set_channel(std::uint32_t /*light_index*/,
                   const LightChannelParam& /*param*/) override {
    return false;
  }

  LightHealth get_health() const override {
    LightHealth health;
    health.ok = false;
    health.ready = false;
    health.message = unsupported_driver_message("Light", backend_);
    return health;
  }

  void shutdown_all() override {}

private:
  void set_error(std::string* error_message) const {
    if (error_message != nullptr) {
      *error_message = unsupported_driver_message("Light", backend_);
    }
  }

  HardwareBackend backend_;
};

class UnsupportedCamera final : public ICamera {
public:
  explicit UnsupportedCamera(HardwareBackend backend) : backend_(backend) {}

  bool initialize(const CameraConfig& /*config*/) override {
    return false;
  }

  void start() override {}
  void stop() override {}

  bool arm(std::uint64_t /*trigger_id*/,
           const LightChannelParam& /*light_param*/,
           std::uint32_t /*light_seq_index*/,
           int /*timeout_ms*/) override {
    return false;
  }

  bool simulate_exposure_output(std::uint64_t /*trigger_id*/,
                                const LightChannelParam& /*light_param*/,
                                std::uint32_t /*light_seq_index*/,
                                int /*timeout_ms*/) override {
    return false;
  }

  bool wait_frame(std::uint64_t /*trigger_id*/,
                  const LightChannelParam& /*light_param*/,
                  std::uint32_t /*light_seq_index*/,
                  CapturedFrame* /*out_frame*/,
                  int /*timeout_ms*/) override {
    return false;
  }

  CameraHealth get_health() const override {
    return CameraHealth{false, 0, unsupported_driver_message("Camera", backend_)};
  }

private:
  HardwareBackend backend_;
};

class UnsupportedRobotClient final : public IRobotClient {
public:
  explicit UnsupportedRobotClient(HardwareBackend backend) : backend_(backend) {}

  bool initialize(const RobotClientConfig& /*config*/) override {
    return false;
  }

  bool wait_pose_ready(const ExternalTrigger& /*trigger*/,
                       const RobotPoseRequest& /*request*/,
                       int /*timeout_ms*/,
                       RobotPoseStatus* /*out_status*/,
                       std::string* error_message) override {
    if (error_message != nullptr) {
      *error_message = unsupported_driver_message("Robot", backend_);
    }
    return false;
  }

  RobotHealth get_health() const override {
    return RobotHealth{false, unsupported_driver_message("Robot", backend_)};
  }

private:
  HardwareBackend backend_;
};

}  // namespace detail

inline std::unique_ptr<ISignalClient> create_signal_client(HardwareBackend backend) {
  if (is_manual_trigger_backend(backend)) {
    return std::make_unique<ManualSignalClient>();
  }
  if (is_external_signal_backend(backend)) {
    return std::make_unique<ExternalSignalClient>();
  }
  if (backend == HardwareBackend::TcpSignal) {
    return std::make_unique<TcpSignalClient>();
  }
  if (!is_simulated_backend(backend)) {
    return std::make_unique<detail::UnsupportedSignalClient>(backend);
  }
  return std::make_unique<SimSignalClient>();
}

inline std::unique_ptr<ILightController> create_light_controller(HardwareBackend backend) {
  if (backend == HardwareBackend::SerialAscii) {
    return std::make_unique<FlAcdhLightController>();
  }
  if (!is_simulated_backend(backend)) {
    return std::make_unique<detail::UnsupportedLightController>(backend);
  }
  return std::make_unique<SimLightController>();
}

inline std::unique_ptr<ICamera> create_camera(HardwareBackend backend) {
  if (backend == HardwareBackend::HikrobotMvs) {
    return std::make_unique<HikrobotMvsCamera>();
  }
  if (!is_simulated_backend(backend)) {
    return std::make_unique<detail::UnsupportedCamera>(backend);
  }
  return std::make_unique<SimCamera>();
}

inline std::unique_ptr<IRobotClient> create_robot_client(HardwareBackend backend) {
  if (!is_simulated_backend(backend)) {
    return std::make_unique<detail::UnsupportedRobotClient>(backend);
  }
  return std::make_unique<SimRobotClient>();
}

}  // namespace seat_aoi
