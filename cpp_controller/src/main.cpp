#include <cstdlib>
#include <iostream>
#include <string>

#include "control/station_controller.hpp"
#include "control/station_runtime_config.hpp"
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
  config.simulate_light_fault = has_arg(argc, argv, "--simulate-light-fault");
  config.simulate_missing_frame = has_arg(argc, argv, "--simulate-missing-frame");
  config.simulate_plc_output_fault = has_arg(argc, argv, "--simulate-plc-output-fault");

  for (int i = 1; i + 1 < argc; ++i) {
    if (std::string(argv[i]) == "--config") {
      seat_aoi::StationRuntimeConfig runtime_config;
      std::string error;
      if (!seat_aoi::load_station_runtime_config(argv[i + 1], &runtime_config, &error)) {
        std::cerr << error << std::endl;
        return 2;
      }
      config.reset_shared_memory = runtime_config.reset_shared_memory;
      config.slot_count = runtime_config.slot_count;
      config.frame_slot_size = runtime_config.frame_slot_size;
      config.result_slot_size = runtime_config.result_slot_size;
      config.publish_timeout_ms = runtime_config.publish_timeout_ms;
      config.detector_timeout_ms = runtime_config.detector_timeout_ms;
      config.camera_timeout_ms = runtime_config.camera_timeout_ms;
      config.light_timeout_ms = runtime_config.light_timeout_ms;
      config.simulate_light_fault = runtime_config.light.simulate_fault;
      config.simulate_plc_output_fault = runtime_config.plc.simulate_output_fault;
      for (const auto& camera : runtime_config.cameras) {
        config.simulate_missing_frame = config.simulate_missing_frame || camera.simulate_missing_frame;
      }
    }
  }

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
