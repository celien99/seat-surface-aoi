#pragma once

#include <cstdint>
#include <string>

#include "common/inspection_types.hpp"
#include "control/trigger_scheduler.hpp"

namespace seat_aoi {

struct PlcHealth {
  bool ok = true;
  std::string message = "simulated";
};

class PlcClient {
public:
  bool initialize(bool simulate_output_fault);
  bool send_decision(const PlcTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message);
  PlcHealth get_health() const;

private:
  bool initialized_ = false;
  bool simulate_output_fault_ = false;
};

}  // namespace seat_aoi

