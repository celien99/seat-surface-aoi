#include "control/light_controller.hpp"

#include <chrono>
#include <iostream>
#include <thread>

namespace seat_aoi {

bool LightController::initialize(bool simulate_fault) {
  initialized_ = true;
  simulate_fault_ = simulate_fault;
  return true;
}

bool LightController::prepare_sequence(const LightSequence& sequence,
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

bool LightController::trigger_channel(const LightChannelParam& channel,
                                      std::uint64_t trigger_id,
                                      std::uint32_t light_seq_index,
                                      int timeout_ms,
                                      std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 || channel.light_index == 0 ||
      channel.exposure_us == 0 || channel.current_percent <= 0.0F) {
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
            << " light_seq_index=" << light_seq_index << "] simulated strobe exposure_us="
            << channel.exposure_us << " gain=" << channel.gain
            << " current_percent=" << channel.current_percent << std::endl;
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  return true;
}

bool LightController::arm_hardware_trigger(const LightChannelParam& channel,
                                           std::uint64_t trigger_id,
                                           std::uint32_t light_seq_index,
                                           int timeout_ms,
                                           std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 || channel.light_index == 0 ||
      channel.exposure_us == 0 || channel.current_percent <= 0.0F) {
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
  std::cout << "[trigger_id=" << trigger_id << " light_index=" << channel.light_index
            << " light_seq_index=" << light_seq_index
            << "] arm light for camera exposure hardware trigger exposure_us="
            << channel.exposure_us << " gain=" << channel.gain
            << " current_percent=" << channel.current_percent << std::endl;
  return true;
}

bool LightController::notify_hardware_triggered(const LightChannelParam& channel,
                                                std::uint64_t trigger_id,
                                                std::uint32_t light_seq_index,
                                                int timeout_ms,
                                                std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 || !hardware_trigger_armed_ ||
      armed_light_index_ != channel.light_index) {
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
            << " light_seq_index=" << light_seq_index
            << "] camera exposure output fired strobe" << std::endl;
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  hardware_trigger_armed_ = false;
  armed_light_index_ = 0;
  return true;
}

bool LightController::run_sequence(const LightSequence& sequence,
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

bool LightController::set_channel(std::uint32_t light_index,
                                  const LightChannelParam& param) {
  return initialized_ && light_index == param.light_index && light_index != 0 &&
         param.exposure_us > 0 && param.current_percent > 0.0F;
}

LightHealth LightController::get_health() const {
  return LightHealth{initialized_ && !simulate_fault_, simulate_fault_ ? "模拟光源故障" : "simulated"};
}

void LightController::shutdown_all() {
  initialized_ = false;
  hardware_trigger_armed_ = false;
  armed_light_index_ = 0;
}

}  // namespace seat_aoi
