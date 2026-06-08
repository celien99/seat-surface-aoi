#include <cstdlib>
#include <iostream>
#include <string>

#include "control/station_controller.hpp"
#include "control/trigger_scheduler.hpp"

namespace {

bool has_arg(int argc, char** argv, const std::string& needle) {
  for (int i = 1; i < argc; ++i) {
    if (argv[i] == needle) {
      return true;
    }
  }
  return false;
}

int int_arg(int argc, char** argv, const std::string& name, int fallback) {
  for (int i = 1; i + 1 < argc; ++i) {
    if (argv[i] == name) {
      return std::atoi(argv[i + 1]);
    }
  }
  return fallback;
}

}  // namespace

int main(int argc, char** argv) {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = !has_arg(argc, argv, "--no-reset");
  config.detector_timeout_ms = int_arg(argc, argv, "--wait-ms", 5000);

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "failed to initialize station shared memory" << std::endl;
    return 2;
  }

  if (has_arg(argc, argv, "--cleanup")) {
    station.cleanup_shared_memory();
    return 0;
  }

  seat_aoi::TriggerScheduler scheduler;
  const auto trigger = scheduler.next_simulated_trigger();
  const auto result = station.inspect_one_seat(trigger);
  return result.meta.decision == static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK) ||
                 result.meta.decision ==
                     static_cast<std::uint32_t>(seat_aoi::InspectionDecision::NG) ||
                 result.meta.decision ==
                     static_cast<std::uint32_t>(seat_aoi::InspectionDecision::Recheck)
             ? 0
             : 1;
}

