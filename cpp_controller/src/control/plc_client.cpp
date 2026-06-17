#include "control/plc_client.hpp"

#include <chrono>
#include <iostream>
#include <thread>

namespace seat_aoi {

bool SimPlcClient::initialize(const PlcClientConfig& config) {
  initialized_ = true;
  simulate_output_fault_ = config.simulate_output_fault;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  return true;
}

bool SimPlcClient::wait_trigger(PlcTrigger* out_trigger,
                             int timeout_ms,
                             std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "PLC 未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
    if (error_message != nullptr) {
      *error_message = "模拟 PLC 触发超时";
    }
    return false;
  }

  PlcTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = "SIM_SEAT_" + std::to_string(trigger.trigger_id);
  trigger.sku = "seat_a_black_leather";
  *out_trigger = trigger;
  return true;
}

bool SimPlcClient::send_decision(const PlcTrigger& /*trigger*/,
                              std::uint64_t /*sequence_id*/,
                              InspectionDecision /*decision*/,
                              int timeout_ms,
                              std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "PLC 未初始化或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_output_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟 PLC 输出失败";
    }
    return false;
  }
  return true;
}

PlcHealth SimPlcClient::get_health() const {
  return PlcHealth{initialized_ && !simulate_output_fault_ && !simulate_trigger_timeout_,
                   simulate_output_fault_     ? "模拟 PLC 输出失败"
                   : simulate_trigger_timeout_ ? "模拟 PLC 触发超时"
                                               : "simulated"};
}

bool ManualTriggerPlcClient::initialize(const PlcClientConfig& config) {
  initialized_ = true;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  if (!config.station_id.empty()) {
    station_id_ = config.station_id;
  }
  if (!config.sku_source.empty()) {
    sku_ = config.sku_source;
  }
  return true;
}

bool ManualTriggerPlcClient::wait_trigger(PlcTrigger* out_trigger,
                                          int timeout_ms,
                                          std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "手动触发 PLC 未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
    if (error_message != nullptr) {
      *error_message = "手动触发超时";
    }
    return false;
  }

  PlcTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = station_id_ + "_MANUAL_SEAT_" + std::to_string(trigger.trigger_id);
  trigger.sku = sku_;
  *out_trigger = trigger;
  std::cout << "[trigger_id=" << trigger.trigger_id
            << "] manual trigger accepted seat_id=" << trigger.seat_id
            << " sku=" << trigger.sku << std::endl;
  return true;
}

bool ManualTriggerPlcClient::send_decision(const PlcTrigger& trigger,
                                           std::uint64_t sequence_id,
                                           InspectionDecision decision,
                                           int timeout_ms,
                                           std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "手动触发 PLC 未初始化或 timeout_ms 非法";
    }
    return false;
  }
  std::cout << "[sequence_id=" << sequence_id << " trigger_id=" << trigger.trigger_id
            << "] manual trigger decision recorded decision="
            << static_cast<std::uint32_t>(decision) << std::endl;
  return true;
}

PlcHealth ManualTriggerPlcClient::get_health() const {
  return PlcHealth{initialized_ && !simulate_trigger_timeout_,
                   simulate_trigger_timeout_ ? "手动触发超时" : "manual_trigger"};
}

}  // namespace seat_aoi
