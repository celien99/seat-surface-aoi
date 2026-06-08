#pragma once

#include <cstdint>
#include <string>

namespace seat_aoi {

struct PlcTrigger {
  std::uint64_t trigger_id = 0;
  std::string seat_id = "SIM_SEAT_001";
  std::string sku = "seat_a_black_leather";
};

class TriggerScheduler {
public:
  PlcTrigger next_simulated_trigger();

private:
  std::uint64_t next_trigger_id_ = 1000;
};

}  // namespace seat_aoi
