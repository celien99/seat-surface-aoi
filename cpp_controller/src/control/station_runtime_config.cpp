#include "control/station_runtime_config.hpp"

#include <fstream>
#include <sstream>
#include <string>

namespace seat_aoi {

namespace {

bool parse_bool(const std::string& value) {
  return value == "true" || value == "1" || value == "yes";
}

std::string trim(const std::string& value) {
  const auto begin = value.find_first_not_of(" \t\r\n");
  if (begin == std::string::npos) {
    return "";
  }
  const auto end = value.find_last_not_of(" \t\r\n");
  return value.substr(begin, end - begin + 1);
}

}  // namespace

bool load_station_runtime_config(const std::string& path,
                                 StationRuntimeConfig* out_config,
                                 std::string* error_message) {
  if (out_config == nullptr) {
    return false;
  }
  std::ifstream input(path);
  if (!input.is_open()) {
    if (error_message != nullptr) {
      *error_message = "运行配置文件不存在: " + path;
    }
    return false;
  }

  StationRuntimeConfig config;
  std::string line;
  while (std::getline(input, line)) {
    const auto comment = line.find('#');
    if (comment != std::string::npos) {
      line = line.substr(0, comment);
    }
    line = trim(line);
    if (line.empty()) {
      continue;
    }
    const auto eq = line.find('=');
    if (eq == std::string::npos) {
      if (error_message != nullptr) {
        *error_message = "运行配置行缺少 =: " + line;
      }
      return false;
    }
    const std::string key = trim(line.substr(0, eq));
    const std::string value = trim(line.substr(eq + 1));
    if (key == "detector_timeout_ms") {
      config.detector_timeout_ms = std::stoi(value);
    } else if (key == "publish_timeout_ms") {
      config.publish_timeout_ms = std::stoi(value);
    } else if (key == "camera_timeout_ms") {
      config.camera_timeout_ms = std::stoi(value);
    } else if (key == "light_timeout_ms") {
      config.light_timeout_ms = std::stoi(value);
    } else if (key == "reset_shared_memory") {
      config.reset_shared_memory = parse_bool(value);
    } else if (key == "simulate_light_fault") {
      config.light.simulate_fault = parse_bool(value);
    } else if (key == "simulate_plc_output_fault") {
      config.plc.simulate_output_fault = parse_bool(value);
    } else if (key == "simulate_missing_frame") {
      for (auto& camera : config.cameras) {
        camera.simulate_missing_frame = parse_bool(value);
      }
    } else if (key == "trace_root") {
      config.trace_root = value;
    }
  }
  *out_config = config;
  return true;
}

}  // namespace seat_aoi

