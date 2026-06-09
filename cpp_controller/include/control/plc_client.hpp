#pragma once

#include "control/iplc_client.hpp"

namespace seat_aoi {

class SimPlcClient : public IPlcClient {
public:
  bool initialize(bool simulate_output_fault,
                  bool simulate_trigger_timeout = false) override;
  bool wait_trigger(PlcTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool send_decision(const PlcTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message) override;
  PlcHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_output_fault_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 1000;
};

}  // namespace seat_aoi
