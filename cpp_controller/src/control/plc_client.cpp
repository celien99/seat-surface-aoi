#include "control/plc_client.hpp"

#include <chrono>
#include <thread>

namespace seat_aoi {

bool PlcClient::initialize(bool simulate_output_fault, bool simulate_trigger_timeout) {
  initialized_ = true;
  simulate_output_fault_ = simulate_output_fault;
  simulate_trigger_timeout_ = simulate_trigger_timeout;
  return true;
}

bool PlcClient::wait_trigger(PlcTrigger* out_trigger,
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

bool PlcClient::send_decision(const PlcTrigger& /*trigger*/,
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

PlcHealth PlcClient::get_health() const {
  return PlcHealth{initialized_ && !simulate_output_fault_ && !simulate_trigger_timeout_,
                   simulate_output_fault_     ? "模拟 PLC 输出失败"
                   : simulate_trigger_timeout_ ? "模拟 PLC 触发超时"
                                               : "simulated"};
}

}  // namespace seat_aoi
