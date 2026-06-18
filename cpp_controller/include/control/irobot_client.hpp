#pragma once

#include <cstdint>
#include <string>

#include "control/hardware_backend.hpp"
#include "control/trigger_scheduler.hpp"

namespace seat_aoi {

struct RobotClientConfig {
  HardwareBackend backend = HardwareBackend::Simulated;
  std::string controller_id;
  std::string host;
  std::uint32_t port = 0;
  std::string ready_input;
  std::string fault_input;
  std::string start_output;
  bool simulate_fault = false;
};

struct RobotPoseRequest {
  std::uint32_t pose_index = 0;
  std::string pose_id;
  std::string shot_id_source;
  std::string ready_input;
  std::string fault_input;
  std::string photo_trigger_input;
  std::uint64_t simulated_shot_id = 0;
  float planned_tcp_xyz_mm[3] = {0.0F, 0.0F, 0.0F};
  float planned_rpy_deg[3] = {0.0F, 0.0F, 0.0F};
};

struct RobotPoseStatus {
  bool ready = false;
  bool fault = false;
  std::uint64_t shot_id = 0;
  std::uint64_t robot_timestamp_us = 0;
  float tcp_xyz_mm[3] = {0.0F, 0.0F, 0.0F};
  float rpy_deg[3] = {0.0F, 0.0F, 0.0F};
  std::string message;
};

struct RobotHealth {
  bool ok = true;
  std::string message = "simulated";
};

class IRobotClient {
public:
  virtual ~IRobotClient() = default;
  virtual bool initialize(const RobotClientConfig& config) = 0;
  virtual bool wait_pose_ready(const ExternalTrigger& trigger,
                               const RobotPoseRequest& request,
                               int timeout_ms,
                               RobotPoseStatus* out_status,
                               std::string* error_message) = 0;
  virtual RobotHealth get_health() const = 0;
};

}  // namespace seat_aoi
