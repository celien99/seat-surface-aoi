#include "control/station_runtime_config.hpp"

#include <exception>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
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

bool parse_light_order(const std::string& value,
                       std::vector<std::uint32_t>* out_light_order,
                       std::string* error_message) {
  std::vector<std::uint32_t> light_order;
  std::stringstream stream(value);
  std::string item;
  while (std::getline(stream, item, ',')) {
    item = trim(item);
    if (item.empty()) {
      continue;
    }
    try {
      const int parsed = std::stoi(item);
      if (parsed <= 0) {
        if (error_message != nullptr) {
          *error_message = "light_order 只能包含正整数: " + value;
        }
        return false;
      }
      light_order.push_back(static_cast<std::uint32_t>(parsed));
    } catch (const std::exception&) {
      if (error_message != nullptr) {
        *error_message = "light_order 解析失败: " + value;
      }
      return false;
    }
  }
  if (light_order.empty()) {
    if (error_message != nullptr) {
      *error_message = "light_order 不能为空";
    }
    return false;
  }
  *out_light_order = light_order;
  return true;
}

bool parse_trigger_sync_mode(const std::string& value,
                             TriggerSyncMode* out_mode,
                             std::string* error_message) {
  if (value == "camera_exposure_output" || value == "hardware" ||
      value == "hard_trigger") {
    *out_mode = TriggerSyncMode::CameraExposureOutput;
    return true;
  }
  if (value == "software") {
    *out_mode = TriggerSyncMode::Software;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "trigger_sync_mode 只能是 camera_exposure_output 或 software: " + value;
  }
  return false;
}

RuntimeLightChannelConfig default_light_channel_config(std::uint32_t light_index) {
  RuntimeLightChannelConfig config;
  config.light_index = light_index;
  config.physical_channel = light_index;
  return config;
}

RuntimeLightChannelConfig* ensure_light_channel(
    std::map<std::uint32_t, RuntimeLightChannelConfig>* channels,
    std::uint32_t light_index) {
  auto iter = channels->find(light_index);
  if (iter == channels->end()) {
    iter = channels->emplace(light_index, default_light_channel_config(light_index)).first;
  }
  return &iter->second;
}

bool parse_light_channel_key(const std::string& key,
                             std::uint32_t* out_light_index,
                             std::string* out_field) {
  constexpr const char* kPrefix = "light.";
  if (key.rfind(kPrefix, 0) != 0) {
    return false;
  }
  const auto after_prefix = std::string(kPrefix).size();
  const auto field_separator = key.find('.', after_prefix);
  if (field_separator == std::string::npos) {
    return false;
  }
  const std::string index_text = key.substr(after_prefix, field_separator - after_prefix);
  try {
    const int parsed = std::stoi(index_text);
    if (parsed <= 0) {
      return false;
    }
    *out_light_index = static_cast<std::uint32_t>(parsed);
    *out_field = key.substr(field_separator + 1);
    return !out_field->empty();
  } catch (const std::exception&) {
    return false;
  }
}

bool apply_light_channel_value(RuntimeLightChannelConfig* channel,
                               const std::string& field,
                               const std::string& value,
                               std::string* error_message) {
  try {
    if (field == "physical_channel") {
      const int parsed = std::stoi(value);
      if (parsed <= 0) {
        throw std::invalid_argument("physical_channel");
      }
      channel->physical_channel = static_cast<std::uint32_t>(parsed);
    } else if (field == "exposure_us") {
      const int parsed = std::stoi(value);
      if (parsed <= 0) {
        throw std::invalid_argument("exposure_us");
      }
      channel->exposure_us = static_cast<std::uint32_t>(parsed);
    } else if (field == "strobe_width_us") {
      const int parsed = std::stoi(value);
      if (parsed <= 0) {
        throw std::invalid_argument("strobe_width_us");
      }
      channel->strobe_width_us = static_cast<std::uint32_t>(parsed);
    } else if (field == "trigger_delay_us") {
      const int parsed = std::stoi(value);
      if (parsed < 0) {
        throw std::invalid_argument("trigger_delay_us");
      }
      channel->trigger_delay_us = static_cast<std::uint32_t>(parsed);
    } else if (field == "gain") {
      const float parsed = std::stof(value);
      if (parsed <= 0.0F) {
        throw std::invalid_argument("gain");
      }
      channel->gain = parsed;
    } else if (field == "current_percent") {
      const float parsed = std::stof(value);
      if (parsed <= 0.0F || parsed > 100.0F) {
        throw std::invalid_argument("current_percent");
      }
      channel->current_percent = parsed;
    } else {
      if (error_message != nullptr) {
        *error_message = "未知光源配置字段: light." + std::to_string(channel->light_index) +
                         "." + field;
      }
      return false;
    }
  } catch (const std::exception&) {
    if (error_message != nullptr) {
      *error_message = "光源配置字段非法: light." + std::to_string(channel->light_index) +
                       "." + field + "=" + value;
    }
    return false;
  }
  return true;
}

bool validate_light_channels(const std::vector<std::uint32_t>& light_order,
                             const std::map<std::uint32_t, RuntimeLightChannelConfig>& channels,
                             std::string* error_message) {
  for (std::uint32_t light_index : light_order) {
    const auto iter = channels.find(light_index);
    if (iter == channels.end()) {
      if (error_message != nullptr) {
        *error_message = "light_order 中的光源缺少配置: light." +
                         std::to_string(light_index);
      }
      return false;
    }
    const auto& channel = iter->second;
    if (channel.physical_channel == 0 || channel.exposure_us == 0 ||
        channel.strobe_width_us == 0 || channel.gain <= 0.0F ||
        channel.current_percent <= 0.0F || channel.current_percent > 100.0F) {
      if (error_message != nullptr) {
        *error_message = "光源配置非法: light." + std::to_string(light_index);
      }
      return false;
    }
  }
  return true;
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
  std::map<std::uint32_t, RuntimeLightChannelConfig> light_channels;
  for (const auto& channel : config.light_channels) {
    light_channels[channel.light_index] = channel;
  }
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
    } else if (key == "trigger_timeout_ms") {
      config.trigger_timeout_ms = std::stoi(value);
    } else if (key == "publish_timeout_ms") {
      config.publish_timeout_ms = std::stoi(value);
    } else if (key == "camera_timeout_ms") {
      config.camera_timeout_ms = std::stoi(value);
    } else if (key == "light_timeout_ms") {
      config.light_timeout_ms = std::stoi(value);
    } else if (key == "recipe_id") {
      config.recipe_id = value;
    } else if (key == "max_jobs") {
      config.max_jobs = std::stoi(value);
    } else if (key == "light_order") {
      if (!parse_light_order(value, &config.light_order, error_message)) {
        return false;
      }
    } else if (key == "trigger_sync_mode") {
      if (!parse_trigger_sync_mode(value, &config.trigger_sync_mode, error_message)) {
        return false;
      }
    } else if (key == "reset_shared_memory") {
      config.reset_shared_memory = parse_bool(value);
    } else if (key == "simulate_light_fault") {
      config.light.simulate_fault = parse_bool(value);
    } else if (key == "simulate_plc_output_fault") {
      config.plc.simulate_output_fault = parse_bool(value);
    } else if (key == "simulate_trigger_timeout") {
      config.plc.simulate_trigger_timeout = parse_bool(value);
    } else if (key == "simulate_missing_frame") {
      for (auto& camera : config.cameras) {
        camera.simulate_missing_frame = parse_bool(value);
      }
    } else if (key == "trace_root") {
      config.trace_root = value;
    } else {
      std::uint32_t light_index = 0;
      std::string light_field;
      if (parse_light_channel_key(key, &light_index, &light_field)) {
        auto* channel = ensure_light_channel(&light_channels, light_index);
        if (!apply_light_channel_value(channel, light_field, value, error_message)) {
          return false;
        }
      }
    }
  }
  if (!validate_light_channels(config.light_order, light_channels, error_message)) {
    return false;
  }
  config.light_channels.clear();
  for (const auto& [light_index, channel] : light_channels) {
    (void)light_index;
    config.light_channels.push_back(channel);
  }
  *out_config = config;
  return true;
}

}  // namespace seat_aoi
