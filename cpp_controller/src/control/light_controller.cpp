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

bool SimLightController::initialize(bool simulate_fault) {
  initialized_ = true;
  simulate_fault_ = simulate_fault;
  hardware_trigger_armed_ = false;
  armed_light_index_ = 0;
  armed_physical_channel_ = 0;
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
    if (error_message != nullptr) {
      *error_message = "光源未初始化、序列为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟光源故障";
    }
    shutdown_all();
    return false;
  }
  for (const auto& channel : sequence.channels) {
    if (!set_channel(channel.light_index, channel)) {
      if (error_message != nullptr) {
        *error_message = "光源通道参数配置失败";
      }
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
  if (!initialized_ || timeout_ms <= 0 || !valid_channel_param(channel)) {
    if (error_message != nullptr) {
      *error_message = "光源通道触发参数非法";
    }
    shutdown_all();
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟光源故障";
    }
    shutdown_all();
    return false;
  }
  std::cout << "[trigger_id=" << trigger_id << " light_index=" << channel.light_index
            << " physical_channel=" << channel.physical_channel
            << " light_seq_index=" << light_seq_index << "] simulated strobe exposure_us="
            << channel.exposure_us << " strobe_width_us=" << channel.strobe_width_us
            << " trigger_delay_us=" << channel.trigger_delay_us << " gain=" << channel.gain
            << " current_percent=" << channel.current_percent << std::endl;
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  ++trigger_count_;
  last_light_index_ = channel.light_index;
  last_physical_channel_ = channel.physical_channel;
  return true;
}

bool SimLightController::arm_hardware_trigger(const LightChannelParam& channel,
                                           std::uint64_t trigger_id,
                                           std::uint32_t light_seq_index,
                                           int timeout_ms,
                                           std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 || !valid_channel_param(channel)) {
    if (error_message != nullptr) {
      *error_message = "光源硬触发 arm 参数非法";
    }
    shutdown_all();
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟光源故障";
    }
    shutdown_all();
    return false;
  }
  hardware_trigger_armed_ = true;
  armed_light_index_ = channel.light_index;
  armed_physical_channel_ = channel.physical_channel;
  std::cout << "[trigger_id=" << trigger_id << " light_index=" << channel.light_index
            << " physical_channel=" << channel.physical_channel
            << " light_seq_index=" << light_seq_index
            << "] arm light for camera exposure hardware trigger exposure_us="
            << channel.exposure_us << " strobe_width_us=" << channel.strobe_width_us
            << " trigger_delay_us=" << channel.trigger_delay_us << " gain=" << channel.gain
            << " current_percent=" << channel.current_percent << std::endl;
  return true;
}

bool SimLightController::notify_hardware_triggered(const LightChannelParam& channel,
                                                std::uint64_t trigger_id,
                                                std::uint32_t light_seq_index,
                                                int timeout_ms,
                                                std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 || !hardware_trigger_armed_ ||
      armed_light_index_ != channel.light_index ||
      armed_physical_channel_ != channel.physical_channel) {
    if (error_message != nullptr) {
      *error_message = "光源硬触发未 arm 或通道不匹配";
    }
    shutdown_all();
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟光源故障";
    }
    shutdown_all();
    return false;
  }
  std::cout << "[trigger_id=" << trigger_id << " light_index=" << channel.light_index
            << " physical_channel=" << channel.physical_channel
            << " light_seq_index=" << light_seq_index
            << "] camera exposure output fired strobe" << std::endl;
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  hardware_trigger_armed_ = false;
  armed_light_index_ = 0;
  armed_physical_channel_ = 0;
  ++trigger_count_;
  last_light_index_ = channel.light_index;
  last_physical_channel_ = channel.physical_channel;
  return true;
}

bool SimLightController::run_sequence(const LightSequence& sequence,
                                   std::uint64_t trigger_id,
                                   int timeout_ms,
                                   std::string* error_message) {
  if (!prepare_sequence(sequence, trigger_id, timeout_ms, error_message)) {
    return false;
  }
  for (std::uint32_t i = 0; i < sequence.channels.size(); ++i) {
    if (!trigger_channel(sequence.channels[i], trigger_id, i, timeout_ms, error_message)) {
      return false;
    }
  }
  return true;
}

bool SimLightController::set_channel(std::uint32_t light_index,
                                     const LightChannelParam& param) {
  return initialized_ && light_index == param.light_index && light_index != 0 &&
         valid_channel_param(param);
}

LightHealth SimLightController::get_health() const {
  LightHealth health;
  health.ok = initialized_ && !simulate_fault_;
  health.ready = initialized_ && !simulate_fault_ && !hardware_trigger_armed_;
  health.over_current = false;
  health.over_temperature = false;
  health.trigger_missed = false;
  health.trigger_count = trigger_count_;
  health.last_light_index = last_light_index_;
  health.last_physical_channel = last_physical_channel_;
  health.message = simulate_fault_ ? "模拟光源故障" : "simulated";
  return health;
}

void SimLightController::shutdown_all() {
  initialized_ = false;
  hardware_trigger_armed_ = false;
  armed_light_index_ = 0;
  armed_physical_channel_ = 0;
}

}  // namespace seat_aoi
