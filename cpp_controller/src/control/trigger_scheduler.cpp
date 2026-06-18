#include "control/trigger_scheduler.hpp"

namespace seat_aoi {

ExternalTrigger TriggerScheduler::next_simulated_trigger() {
  ExternalTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = "SIM_SEAT_001";
  trigger.sku = "seat_a_black_leather";
  return trigger;
}

}  // namespace seat_aoi

