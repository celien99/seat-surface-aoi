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
  config->hardware_mode = runtime_config.hardware_mode;
  config->camera_backend = runtime_config.camera_backend;
  config->reset_shared_memory = runtime_config.reset_shared_memory;
  config->slot_count = runtime_config.slot_count;
  config->frame_slot_size = runtime_config.frame_slot_size;
  config->result_slot_size = runtime_config.result_slot_size;
  config->publish_timeout_ms = runtime_config.publish_timeout_ms;
  config->detector_timeout_ms = runtime_config.detector_timeout_ms;
  config->trigger_timeout_ms = runtime_config.trigger_timeout_ms;
  config->camera_timeout_ms = runtime_config.camera_timeout_ms;
  config->light_timeout_ms = runtime_config.light_timeout_ms;
  config->warning_recheck_threshold = runtime_config.warning_recheck_threshold;
  config->critical_recheck_threshold = runtime_config.critical_recheck_threshold;
  config->max_jobs = runtime_config.max_jobs;
  config->recipe_id = runtime_config.recipe_id;
  config->trace_root = runtime_config.trace_root;
  config->light_order = runtime_config.light_order;
  config->capture_mode = runtime_config.capture_mode;
  config->capture_schedule = runtime_config.capture_schedule;
  config->cameras = runtime_config.cameras;
  config->light = runtime_config.lights.empty() ? seat_aoi::RuntimeLightConfig{} : runtime_config.lights[0];
  config->lights = runtime_config.lights;
  config->light_channels = runtime_config.light_channels;
  config->capture_views = runtime_config.capture_views;
  config->signal = runtime_config.signal;
  config->robot = runtime_config.robot;
  config->simulate_light_fault = !runtime_config.lights.empty() && runtime_config.lights[0].simulate_fault;
  config->robot.simulate_fault = runtime_config.robot.simulate_fault;
  config->simulate_trigger_timeout = runtime_config.signal.simulate_trigger_timeout;
  config->simulate_signal_result_fault = runtime_config.signal.simulate_output_fault;
  for (const auto& camera : runtime_config.cameras) {
    config->simulate_missing_frame = config->simulate_missing_frame || camera.simulate_missing_frame;
  }
  config->image_save = runtime_config.image_save;
  config->json_output_enabled = runtime_config.json_output_enabled;
  config->json_output_host = runtime_config.json_output_host;
  config->json_output_port = runtime_config.json_output_port;
}

}  // namespace

int main(int argc, char** argv) {
  seat_aoi::StationConfig config;
  seat_aoi::StationRuntimeConfig runtime_config;
  const char* config_path = string_arg(argc, argv, "--config");
  if (config_path != nullptr) {
    std::string error;
    if (!seat_aoi::load_station_runtime_config(config_path, &runtime_config, &error)) {
      std::cerr << error << std::endl;
      return 2;
    }
    apply_runtime_config(runtime_config, &config);
  }

  if (has_arg(argc, argv, "--validate-config")) {
    std::string error;
    if (config_path == nullptr &&
        !seat_aoi::validate_station_runtime_config(runtime_config, &error)) {
      std::cerr << error << std::endl;
      return 2;
    }
    std::cout << "C++ station runtime config OK";
    if (config_path != nullptr) {
      std::cout << ": " << config_path;
    }
    std::cout << std::endl;
    return 0;
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
  if (has_value_arg(argc, argv, "--trace-root")) {
    config.trace_root = string_arg(argc, argv, "--trace-root");
  }
  config.simulate_light_fault =
      config.simulate_light_fault || has_arg(argc, argv, "--simulate-light-fault");
  config.simulate_missing_frame =
      config.simulate_missing_frame || has_arg(argc, argv, "--simulate-missing-frame");
  config.simulate_signal_result_fault =
      config.simulate_signal_result_fault ||
      has_arg(argc, argv, "--simulate-signal-result-fault") ||
      has_arg(argc, argv, "--simulate-plc-output-fault");
  config.simulate_trigger_timeout =
      config.simulate_trigger_timeout || has_arg(argc, argv, "--simulate-trigger-timeout");

  const bool loop_mode = has_arg(argc, argv, "--loop") && !has_arg(argc, argv, "--once");
  if (!loop_mode) {
    config.max_jobs = 1;
  }

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "failed to initialize station controller" << std::endl;
    return 2;
  }

  if (has_arg(argc, argv, "--cleanup")) {
    station.cleanup_shared_memory();
    return 0;
  }

  int processed_jobs = 0;
  while (config.max_jobs <= 0 || processed_jobs < config.max_jobs) {
    seat_aoi::ExternalTrigger trigger;
    std::string error;
    if (!station.wait_for_trigger(&trigger, &error)) {
      std::cerr << "external signal trigger wait failed: " << error << std::endl;
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
  const auto health = station.health_snapshot();
  std::cout << "station_state=" << seat_aoi::station_state_name(health.state)
            << " alarm_level=" << seat_aoi::alarm_level_name(health.alarm_level)
            << " total_jobs=" << health.total_jobs
            << " recheck_count=" << health.recheck_count
            << " consecutive_recheck_count=" << health.consecutive_recheck_count
            << std::endl;
  return 0;
}
