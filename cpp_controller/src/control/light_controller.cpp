#include "control/light_controller.hpp"

#include <chrono>
#include <iostream>
#include <thread>

namespace seat_aoi {

namespace {

bool valid_channel_param(const LightChannelParam& channel) {
  return channel.light_index != 0 && channel.physical_channel != 0 &&
         channel.exposure_us != 0 && channel.strobe_width_us != 0 &&
         channel.current_percent > 0.0F && channel.current_percent <= 100.0F;
}

}  // namespace

bool SimLightController::initialize(const LightControllerConfig& config) {
  initialized_ = true;
  simulate_fault_ = config.simulate_fault;
  trigger_count_ = 0;
  last_light_index_ = 0;
  last_physical_channel_ = 0;
  return true;
}

bool SimLightController::prepare_sequence(const LightSequence& sequence,
                                          std::uint64_t trigger_id,
                                          int timeout_ms,
                                          std::string* error_message) {
  if (!initialized_ || sequence.channels.empty() || timeout_ms <= 0) {
    if (error_message != nullptr) *error_message = "光源未初始化或序列为空";
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) *error_message = "模拟光源故障";
    shutdown_all();
    return false;
  }
  for (const auto& channel : sequence.channels) {
    if (!channel.enabled) continue;
    if (!valid_channel_param(channel)) {
      if (error_message != nullptr)
        *error_message = "光源通道 light_index=" + std::to_string(channel.light_index) + " 参数非法";
      shutdown_all();
      return false;
    }
  }
  std::cout << "[trigger_id=" << trigger_id << "] prepared light sequence channels="
            << sequence.channels.size() << std::endl;
  return true;
}

bool SimLightController::trigger_channel(const LightChannelParam& channel,
                                         std::uint64_t trigger_id,
                                         std::uint32_t light_seq_index,
                                         int timeout_ms,
                                         std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 || !channel.enabled || !valid_channel_param(channel)) {
    if (error_message != nullptr) *error_message = "光源通道触发参数非法";
    shutdown_all();
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) *error_message = "模拟光源故障";
    shutdown_all();
    return false;
  }
  std::cout << "[trigger_id=" << trigger_id << " light_index=" << channel.light_index
            << " physical_channel=" << channel.physical_channel
            << " light_seq_index=" << light_seq_index << "] simulated strobe exposure_us="
            << channel.exposure_us << " strobe_width_us=" << channel.strobe_width_us
            << " delay_us=" << channel.trigger_delay_us << " gain=" << channel.gain
            << " post_delay_ms=" << channel.post_delay_ms << std::endl;
  std::this_thread::sleep_for(std::chrono::milliseconds(channel.post_delay_ms));
  ++trigger_count_;
  last_light_index_ = channel.light_index;
  last_physical_channel_ = channel.physical_channel;
  return true;
}

LightHealth SimLightController::get_health() const {
  LightHealth health;
  health.ok = initialized_ && !simulate_fault_;
  health.ready = initialized_ && !simulate_fault_;
  health.trigger_count = trigger_count_;
  health.last_light_index = last_light_index_;
  health.last_physical_channel = last_physical_channel_;
  health.message = simulate_fault_ ? "模拟光源故障" : "simulated";
  return health;
}

void SimLightController::shutdown_all() {
  initialized_ = false;
}

}  // namespace seat_aoi
