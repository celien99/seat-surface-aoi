#pragma once

#include "control/ilight_controller.hpp"

namespace seat_aoi {

// TriggerSyncMode, LightChannelParam, LightSequence, LightHealth moved to ilight_controller.hpp

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

using LightController = SimLightController;  // will be removed in Task 4

}  // namespace seat_aoi
