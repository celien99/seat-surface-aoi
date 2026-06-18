#pragma once

#include <string>

namespace seat_aoi {

enum class HardwareMode {
  Simulated,
  Lab,
  Production,
};

enum class HardwareBackend {
  Simulated,
  ManualTrigger,
  ExternalSignal,
  ModbusTcp,
  SiemensS7,
  EthercatIo,
  DigitalIo,
  SerialAscii,
  BaslerPylon,
  HikrobotMvs,
  DahengGalaxy,
  FlirSpinnaker,
  VendorSdk,
  CustomSdk,
  TcpSignal,
};

const char* hardware_mode_name(HardwareMode mode);
const char* hardware_backend_name(HardwareBackend backend);
bool is_simulated_backend(HardwareBackend backend);
bool is_manual_trigger_backend(HardwareBackend backend);
bool is_external_signal_backend(HardwareBackend backend);
bool parse_hardware_mode(const std::string& value,
                         HardwareMode* out_mode,
                         std::string* error_message);
bool parse_hardware_backend(const std::string& value,
                            HardwareBackend* out_backend,
                            std::string* error_message);

}  // namespace seat_aoi
