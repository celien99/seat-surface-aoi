#include "control/hardware_backend.hpp"

namespace seat_aoi {

const char* hardware_mode_name(HardwareMode mode) {
  switch (mode) {
    case HardwareMode::Simulated:
      return "simulated";
    case HardwareMode::Lab:
      return "lab";
    case HardwareMode::Production:
      return "production";
  }
  return "unknown";
}

const char* hardware_backend_name(HardwareBackend backend) {
  switch (backend) {
    case HardwareBackend::Simulated:
      return "simulated";
    case HardwareBackend::ManualTrigger:
      return "manual_trigger";
    case HardwareBackend::ExternalSignal:
      return "external_signal";
    case HardwareBackend::SerialAscii:
      return "serial_ascii";
    case HardwareBackend::HikrobotMvs:
      return "hikrobot_mvs";
    case HardwareBackend::TcpSignal:
      return "tcp_signal";
  }
  return "unknown";
}

bool is_simulated_backend(HardwareBackend backend) {
  return backend == HardwareBackend::Simulated;
}

bool is_manual_trigger_backend(HardwareBackend backend) {
  return backend == HardwareBackend::ManualTrigger;
}

bool is_external_signal_backend(HardwareBackend backend) {
  return backend == HardwareBackend::ExternalSignal;
}

bool parse_hardware_mode(const std::string& value,
                         HardwareMode* out_mode,
                         std::string* error_message) {
  if (value == "simulated" || value == "simulation" || value == "mock") {
    *out_mode = HardwareMode::Simulated;
    return true;
  }
  if (value == "lab" || value == "integration" || value == "manual") {
    *out_mode = HardwareMode::Lab;
    return true;
  }
  if (value == "production" || value == "real" || value == "hardware") {
    *out_mode = HardwareMode::Production;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "hardware_mode 只能是 simulated、lab 或 production: " + value;
  }
  return false;
}

bool parse_hardware_backend(const std::string& value,
                            HardwareBackend* out_backend,
                            std::string* error_message) {
  if (value == "simulated" || value == "simulation" || value == "mock") {
    *out_backend = HardwareBackend::Simulated;
    return true;
  }
  if (value == "manual_trigger" || value == "manual" || value == "keyboard" ||
      value == "button") {
    *out_backend = HardwareBackend::ManualTrigger;
    return true;
  }
  if (value == "external_signal" || value == "external" || value == "line_signal" ||
      value == "normalized_signal") {
    *out_backend = HardwareBackend::ExternalSignal;
    return true;
  }
  if (value == "serial_ascii" || value == "rs232" || value == "rs485" ||
      value == "serial") {
    *out_backend = HardwareBackend::SerialAscii;
    return true;
  }
  if (value == "hikrobot_mvs" || value == "hikrobot" || value == "mvs") {
    *out_backend = HardwareBackend::HikrobotMvs;
    return true;
  }
  if (value == "tcp_signal" || value == "tcp" || value == "tcp_plc") {
    *out_backend = HardwareBackend::TcpSignal;
    return true;
  }
  if (error_message != nullptr) {
    *error_message =
        "硬件 backend 不支持: " + value +
        "，当前只保留 simulated/manual_trigger/external_signal/tcp_signal/"
        "serial_ascii/hikrobot_mvs";
  }
  return false;
}

}  // namespace seat_aoi
