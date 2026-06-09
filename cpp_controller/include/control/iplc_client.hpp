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

class IPlcClient {
public:
  virtual ~IPlcClient() = default;
  virtual bool initialize(bool simulate_output_fault,
                          bool simulate_trigger_timeout = false) = 0;
  virtual bool wait_trigger(PlcTrigger* out_trigger,
                            int timeout_ms,
                            std::string* error_message) = 0;
  virtual bool send_decision(const PlcTrigger& trigger,
                             std::uint64_t sequence_id,
                             InspectionDecision decision,
                             int timeout_ms,
                             std::string* error_message) = 0;
  virtual PlcHealth get_health() const = 0;
};

}  // namespace seat_aoi
