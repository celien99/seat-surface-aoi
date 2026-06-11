#include "control/robot_client.hpp"

#include <chrono>
#include <thread>

#include "common/time_utils.hpp"

namespace seat_aoi {

bool SimRobotClient::initialize(const RobotClientConfig& config) {
  initialized_ = true;
  simulate_fault_ = config.simulate_fault;
  return true;
}

bool SimRobotClient::wait_pose_ready(const PlcTrigger& trigger,
                                     const RobotPoseRequest& request,
                                     int timeout_ms,
                                     RobotPoseStatus* out_status,
                                     std::string* error_message) {
  if (!initialized_ || out_status == nullptr || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "Robot 未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟 Robot FAULT";
    }
    return false;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  out_status->ready = true;
  out_status->fault = false;
  out_status->shot_id = request.simulated_shot_id != 0
                            ? request.simulated_shot_id
                            : trigger.trigger_id * 100U + request.pose_index;
  out_status->robot_timestamp_us = now_us();
  for (int index = 0; index < 3; ++index) {
    out_status->tcp_xyz_mm[index] = request.planned_tcp_xyz_mm[index];
    out_status->rpy_deg[index] = request.planned_rpy_deg[index];
  }
  out_status->message = "simulated pose ready";
  return true;
}

RobotHealth SimRobotClient::get_health() const {
  return RobotHealth{initialized_ && !simulate_fault_,
                     simulate_fault_ ? "模拟 Robot FAULT" : "simulated"};
}

}  // namespace seat_aoi
