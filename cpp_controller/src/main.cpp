#include <cstdlib>
#include <iostream>
#include <string>

#include "control/station_controller.hpp"
#include "control/station_runtime_config.hpp"

namespace {

bool has_arg(int argc, char** argv, const std::string& needle) {
  for (int i = 1; i < argc; ++i) {
    if (argv[i] == needle) {
      return true;
    }
  }
  return false;
}

const char* string_arg(int argc, char** argv, const std::string& name) {
  for (int i = 1; i + 1 < argc; ++i) {
    if (argv[i] == name) {
      return argv[i + 1];
    }
  }
  return nullptr;
}

bool has_value_arg(int argc, char** argv, const std::string& name) {
  return string_arg(argc, argv, name) != nullptr;
}

int int_arg(int argc, char** argv, const std::string& name, int fallback) {
  for (int i = 1; i + 1 < argc; ++i) {
    if (argv[i] == name) {
      return std::atoi(argv[i + 1]);
    }
  }
  return fallback;
}

void apply_runtime_config(const seat_aoi::StationRuntimeConfig& runtime_config,
                          seat_aoi::StationConfig* config) {
  config->reset_shared_memory = runtime_config.reset_shared_memory;
  config->slot_count = runtime_config.slot_count;
  config->frame_slot_size = runtime_config.frame_slot_size;
  config->result_slot_size = runtime_config.result_slot_size;
  config->publish_timeout_ms = runtime_config.publish_timeout_ms;
  config->detector_timeout_ms = runtime_config.detector_timeout_ms;
  config->trigger_timeout_ms = runtime_config.trigger_timeout_ms;
  config->camera_timeout_ms = runtime_config.camera_timeout_ms;
  config->light_timeout_ms = runtime_config.light_timeout_ms;
  config->max_jobs = runtime_config.max_jobs;
  config->recipe_id = runtime_config.recipe_id;
  config->light_order = runtime_config.light_order;
  config->light_channels = runtime_config.light_channels;
  config->trigger_sync_mode = runtime_config.trigger_sync_mode;
  config->simulate_light_fault = runtime_config.light.simulate_fault;
  config->simulate_trigger_timeout = runtime_config.plc.simulate_trigger_timeout;
  config->simulate_plc_output_fault = runtime_config.plc.simulate_output_fault;
  for (const auto& camera : runtime_config.cameras) {
    config->simulate_missing_frame = config->simulate_missing_frame || camera.simulate_missing_frame;
  }
}

}  // namespace

int main(int argc, char** argv) {
  seat_aoi::StationConfig config;
  const char* config_path = string_arg(argc, argv, "--config");
  if (config_path != nullptr) {
    seat_aoi::StationRuntimeConfig runtime_config;
    std::string error;
    if (!seat_aoi::load_station_runtime_config(config_path, &runtime_config, &error)) {
      std::cerr << error << std::endl;
      return 2;
    }
    apply_runtime_config(runtime_config, &config);
  }

  if (has_arg(argc, argv, "--no-reset")) {
    config.reset_shared_memory = false;
  }
  if (has_value_arg(argc, argv, "--wait-ms")) {
    config.detector_timeout_ms = int_arg(argc, argv, "--wait-ms", config.detector_timeout_ms);
  }
  if (has_value_arg(argc, argv, "--trigger-timeout-ms")) {
    config.trigger_timeout_ms =
        int_arg(argc, argv, "--trigger-timeout-ms", config.trigger_timeout_ms);
  }
  if (has_value_arg(argc, argv, "--max-jobs")) {
    config.max_jobs = int_arg(argc, argv, "--max-jobs", config.max_jobs);
  }
  config.simulate_light_fault =
      config.simulate_light_fault || has_arg(argc, argv, "--simulate-light-fault");
  config.simulate_missing_frame =
      config.simulate_missing_frame || has_arg(argc, argv, "--simulate-missing-frame");
  config.simulate_plc_output_fault =
      config.simulate_plc_output_fault || has_arg(argc, argv, "--simulate-plc-output-fault");
  config.simulate_trigger_timeout =
      config.simulate_trigger_timeout || has_arg(argc, argv, "--simulate-trigger-timeout");

  const bool loop_mode = has_arg(argc, argv, "--loop") && !has_arg(argc, argv, "--once");
  if (!loop_mode) {
    config.max_jobs = 1;
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

  int processed_jobs = 0;
  while (config.max_jobs <= 0 || processed_jobs < config.max_jobs) {
    seat_aoi::PlcTrigger trigger;
    std::string error;
    if (!station.wait_for_trigger(&trigger, &error)) {
      std::cerr << "PLC trigger wait failed: " << error << std::endl;
      return 1;
    }
    const auto result = station.inspect_one_seat(trigger);
    const auto decision = static_cast<seat_aoi::InspectionDecision>(result.meta.decision);
    if (decision != seat_aoi::InspectionDecision::OK &&
        decision != seat_aoi::InspectionDecision::NG &&
        decision != seat_aoi::InspectionDecision::Recheck) {
      return 1;
    }
    ++processed_jobs;
  }
  return 0;
}
