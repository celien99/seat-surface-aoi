#include "control/plc_client.hpp"

namespace seat_aoi {

bool PlcClient::initialize(bool simulate_output_fault) {
  initialized_ = true;
  simulate_output_fault_ = simulate_output_fault;
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
  return PlcHealth{initialized_ && !simulate_output_fault_,
                   simulate_output_fault_ ? "模拟 PLC 输出失败" : "simulated"};
}

}  // namespace seat_aoi

