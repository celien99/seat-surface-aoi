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
  std::uint32_t controller_index = 0;  // 所属控制器索引（0-based）
  std::uint32_t light_index = 0;
  std::uint32_t physical_channel = 0;
  std::uint32_t exposure_us = 0;
  std::uint32_t strobe_width_us = 0;
  std::uint32_t trigger_delay_us = 0;
  float gain = 1.0F;
  float current_percent = 0.0F;
};

struct LightSequence {
  std::vector<LightChannelParam> channels;
};

struct LightControllerConfig {
  std::string device_id;
  std::string host;
  std::uint32_t port = 0;
  std::string serial_port;
  std::uint32_t baud_rate = 0;
  std::string trigger_input_line;
  bool simulate_fault = false;
};

struct LightHealth {
  bool ok = true;
  bool ready = true;
  bool over_current = false;
  bool over_temperature = false;
  bool trigger_missed = false;
  std::uint64_t trigger_count = 0;
  std::uint32_t last_light_index = 0;
  std::uint32_t last_physical_channel = 0;
  std::string message = "simulated";
};

class ILightController {
public:
  virtual ~ILightController() = default;
  virtual bool initialize(const LightControllerConfig& config) = 0;
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
