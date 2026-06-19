#pragma once

#include <cstdint>
#include <string>

namespace seat_aoi {

struct ExternalTrigger {
  std::uint64_t trigger_id = 0;
  std::string seat_id = "SIM_SEAT_001";
  std::string sku = "seat_a_black_leather";
};

}  // namespace seat_aoi
