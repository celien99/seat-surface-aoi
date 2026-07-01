#pragma once

#include <memory>
#include <string>

#include "camera/camera_worker.hpp"
#include "camera/hikrobot_mvs_camera.hpp"
#include "control/hardware_backend.hpp"
#include "control/fl_acdh_light_controller.hpp"
#include "control/light_controller.hpp"
#include "control/signal_client.hpp"
#include "control/station_runtime_config.hpp"
#include "control/tcp_signal_client.hpp"

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

  bool initialize(const LightControllerConfig& /*config*/) override { return false; }
  bool prepare_sequence(const LightSequence& /*seq*/, std::uint64_t /*tid*/,
                        int /*t*/, std::string* error_message) override {
    set_error(error_message); return false;
  }
  bool trigger_channel(const LightChannelParam& /*ch*/, std::uint64_t /*tid*/,
                       std::uint32_t /*si*/, int /*t*/,
                       std::string* error_message) override {
    set_error(error_message); return false;
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

  bool wait_frame(std::uint64_t /*trigger_id*/,
                  const LightChannelParam& /*light_param*/,
                  std::uint32_t /*light_seq_index*/,
                  CapturedFrame* /*out_frame*/,
                  int /*timeout_ms*/) override {
    return false;
  }

  void cancel_wait() override {}

  CameraHealth get_health() const override {
    return CameraHealth{false, 0, unsupported_driver_message("Camera", backend_)};
  }

private:
  HardwareBackend backend_;
};

}  // namespace detail

inline SignalClientConfig make_signal_client_config(const RuntimeSignalConfig& config) {
  SignalClientConfig client_config;
  client_config.station_id = config.station_id;
  client_config.default_seat_id = config.default_seat_id;
  client_config.default_sku = config.default_sku;
  client_config.trigger_queue_path = config.trigger_queue_path;
  client_config.result_queue_path = config.result_queue_path;
  client_config.port = config.port;
  client_config.delimiter = config.delimiter;
  client_config.terminator = config.terminator;
  client_config.ok_response = config.ok_response;
  client_config.protocol_mode = config.protocol_mode;
  client_config.start_command = config.start_command;
  client_config.sn_prefix = config.sn_prefix;
  client_config.start_ack = config.start_ack;
  client_config.sn_ack = config.sn_ack;
  client_config.result_host = config.result_host;
  client_config.result_port = config.result_port;
  client_config.result_prefix = config.result_prefix;
  client_config.result_delimiter = config.result_delimiter;
  client_config.ok_text = config.ok_text;
  client_config.ng_text = config.ng_text;
  client_config.recheck_text = config.recheck_text;
  client_config.error_text = config.error_text;
  client_config.publish_results_on_command_channel =
      config.publish_results_on_command_channel;
  client_config.simulate_output_fault = config.simulate_output_fault;
  client_config.simulate_trigger_timeout = config.simulate_trigger_timeout;
  return client_config;
}

inline LightControllerConfig make_light_controller_config(const RuntimeLightConfig& config) {
  LightControllerConfig controller_config;
  controller_config.device_id = config.device_id;
  controller_config.host = config.host;
  controller_config.port = config.port;
  controller_config.serial_port = config.serial_port;
  controller_config.baud_rate = config.baud_rate;
  controller_config.trigger_input_line = config.trigger_input_line;
  controller_config.response_mode = config.response_mode;
  controller_config.simulate_fault = config.simulate_fault;
  return controller_config;
}

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

}  // namespace seat_aoi
