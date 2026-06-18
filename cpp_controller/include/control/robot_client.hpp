#pragma once

#include "control/irobot_client.hpp"

namespace seat_aoi {

class SimRobotClient final : public IRobotClient {
public:
  bool initialize(const RobotClientConfig& config) override;
  bool wait_pose_ready(const ExternalTrigger& trigger,
                       const RobotPoseRequest& request,
                       int timeout_ms,
                       RobotPoseStatus* out_status,
                       std::string* error_message) override;
  RobotHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_fault_ = false;
};

}  // namespace seat_aoi
