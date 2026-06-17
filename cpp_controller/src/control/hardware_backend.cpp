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
    case HardwareBackend::ModbusTcp:
      return "modbus_tcp";
    case HardwareBackend::SiemensS7:
      return "siemens_s7";
    case HardwareBackend::EthercatIo:
      return "ethercat_io";
    case HardwareBackend::DigitalIo:
      return "digital_io";
    case HardwareBackend::SerialAscii:
      return "serial_ascii";
    case HardwareBackend::BaslerPylon:
      return "basler_pylon";
    case HardwareBackend::HikrobotMvs:
      return "hikrobot_mvs";
    case HardwareBackend::DahengGalaxy:
      return "daheng_galaxy";
    case HardwareBackend::FlirSpinnaker:
      return "flir_spinnaker";
    case HardwareBackend::VendorSdk:
      return "vendor_sdk";
    case HardwareBackend::CustomSdk:
      return "custom_sdk";
  }
  return "unknown";
}

bool is_simulated_backend(HardwareBackend backend) {
  return backend == HardwareBackend::Simulated;
}

bool is_manual_trigger_backend(HardwareBackend backend) {
  return backend == HardwareBackend::ManualTrigger;
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
  if (value == "modbus_tcp" || value == "modbus") {
    *out_backend = HardwareBackend::ModbusTcp;
    return true;
  }
  if (value == "siemens_s7" || value == "s7") {
    *out_backend = HardwareBackend::SiemensS7;
    return true;
  }
  if (value == "ethercat_io" || value == "ethercat") {
    *out_backend = HardwareBackend::EthercatIo;
    return true;
  }
  if (value == "digital_io" || value == "io_card" || value == "dio") {
    *out_backend = HardwareBackend::DigitalIo;
    return true;
  }
  if (value == "serial_ascii" || value == "rs232" || value == "rs485" ||
      value == "serial") {
    *out_backend = HardwareBackend::SerialAscii;
    return true;
  }
  if (value == "basler_pylon" || value == "basler") {
    *out_backend = HardwareBackend::BaslerPylon;
    return true;
  }
  if (value == "hikrobot_mvs" || value == "hikrobot" || value == "mvs") {
    *out_backend = HardwareBackend::HikrobotMvs;
    return true;
  }
  if (value == "daheng_galaxy" || value == "daheng" || value == "galaxy") {
    *out_backend = HardwareBackend::DahengGalaxy;
    return true;
  }
  if (value == "flir_spinnaker" || value == "flir" || value == "spinnaker") {
    *out_backend = HardwareBackend::FlirSpinnaker;
    return true;
  }
  if (value == "vendor_sdk" || value == "sdk") {
    *out_backend = HardwareBackend::VendorSdk;
    return true;
  }
  if (value == "custom_sdk" || value == "custom") {
    *out_backend = HardwareBackend::CustomSdk;
    return true;
  }
  if (error_message != nullptr) {
    *error_message =
        "硬件 backend 不支持: " + value +
        "，可选 simulated/manual_trigger/modbus_tcp/siemens_s7/ethercat_io/digital_io/"
        "serial_ascii/basler_pylon/hikrobot_mvs/daheng_galaxy/"
        "flir_spinnaker/vendor_sdk/custom_sdk";
  }
  return false;
}

}  // namespace seat_aoi
