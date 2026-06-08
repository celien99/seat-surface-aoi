#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

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

class LightController {
public:
  bool initialize(bool simulate_fault = false);
  bool run_sequence(const LightSequence& sequence, std::uint64_t trigger_id, int timeout_ms);
  bool set_channel(std::uint32_t light_index, const LightChannelParam& param);
  LightHealth get_health() const;
  void shutdown_all();

private:
  bool initialized_ = false;
  bool simulate_fault_ = false;
};

}  // namespace seat_aoi
