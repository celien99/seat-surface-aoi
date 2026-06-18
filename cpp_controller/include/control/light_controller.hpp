#pragma once

#include "control/ilight_controller.hpp"

namespace seat_aoi {

class SimLightController : public ILightController {
public:
  bool initialize(const LightControllerConfig& config) override;
  bool prepare_sequence(const LightSequence& sequence,
                        std::uint64_t trigger_id,
                        int timeout_ms,
                        std::string* error_message) override;
  bool trigger_channel(const LightChannelParam& channel,
                       std::uint64_t trigger_id,
                       std::uint32_t light_seq_index,
                       int timeout_ms,
                       std::string* error_message) override;
  LightHealth get_health() const override;
  void shutdown_all() override;

private:
  bool initialized_ = false;
  bool simulate_fault_ = false;
  std::uint64_t trigger_count_ = 0;
  std::uint32_t last_light_index_ = 0;
  std::uint32_t last_physical_channel_ = 0;
};

}  // namespace seat_aoi
