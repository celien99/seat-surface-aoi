#include "control/light_controller.hpp"

#include <chrono>
#include <thread>

namespace seat_aoi {

bool LightController::initialize() {
  initialized_ = true;
  return true;
}

bool LightController::run_sequence(const LightSequence& sequence,
                                   std::uint64_t /*trigger_id*/,
                                   int timeout_ms) {
  if (!initialized_ || sequence.channels.empty() || timeout_ms <= 0) {
    return false;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
  return true;
}

bool LightController::set_channel(std::uint32_t /*light_index*/,
                                  const LightChannelParam& /*param*/) {
  return initialized_;
}

LightHealth LightController::get_health() const {
  return LightHealth{initialized_, initialized_ ? "simulated" : "not initialized"};
}

void LightController::shutdown_all() {
  initialized_ = false;
}

}  // namespace seat_aoi

