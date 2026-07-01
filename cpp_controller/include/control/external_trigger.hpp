#pragma once

#include <cstdint>
#include <string>

namespace seat_aoi {

enum class TriggerSource : std::uint32_t {
  External = 0,
  DisplayManual = 1,
};

struct ExternalTrigger {
  std::uint64_t trigger_id = 0;
  std::string seat_id = "SIM_SEAT_001";
  std::string sku = "seat_a_black_leather";
  TriggerSource source = TriggerSource::External;
};

}  // namespace seat_aoi
