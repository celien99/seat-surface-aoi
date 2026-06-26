#include <chrono>
#include <cstdlib>
#include <iostream>
#include <string>
#include <thread>

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
    config = seat_aoi::to_station_config(runtime_config);
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
      std::cerr << "trigger wait fault: " << error << std::endl;
      // wait_for_trigger 内部已有递增退避，此处仅最短间隔兜底
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      continue;
    }
    const auto result = station.inspect_one_seat(trigger);
    const auto decision = static_cast<seat_aoi::InspectionDecision>(result.meta.decision);
    // 防御性检查原始 decision 值的合法性（validate_detector_result 正常已拦截非法值，
    // 此处作为最后一道防线，防止校验被绕过时静默放行）。
    if (decision != seat_aoi::InspectionDecision::OK &&
        decision != seat_aoi::InspectionDecision::NG &&
        decision != seat_aoi::InspectionDecision::Recheck &&
        decision != seat_aoi::InspectionDecision::Error) {
      std::cerr << "[sequence_id=" << result.meta.sequence_id
                << "] unknown detector decision="
                << static_cast<std::uint32_t>(decision)
                << ", treating as RECHECK" << std::endl;
    }
    // Python 内部异常返回 ERROR 或未知值时映射为 RECHECK 避免进程退出，
    // 与 station_controller 中 published_decision 的映射策略一致。
    const bool known_ok = (decision == seat_aoi::InspectionDecision::OK ||
                           decision == seat_aoi::InspectionDecision::NG ||
                           decision == seat_aoi::InspectionDecision::Recheck);
    const auto effective_decision =
        known_ok ? decision : seat_aoi::InspectionDecision::Recheck;
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
