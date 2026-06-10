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

bool parse_acquisition_strategy(const std::string& value,
                                AcquisitionStrategy* out_strategy,
                                std::string* error_message) {
  if (value == "serial_tdm" || value == "serial" ||
      value == "camera_serial_tdm" || value == "per_camera_serial") {
    *out_strategy = AcquisitionStrategy::SerialTdm;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "acquisition_strategy 只能是 serial_tdm；"
                     "禁止多机位并行频闪采集，避免光源互相污染: " + value;
  }
  return false;
}

bool parse_uint32_field(const std::string& field_name,
                        const std::string& value,
                        bool allow_zero,
                        std::uint32_t* out_value,
                        std::string* error_message) {
  try {
    const int parsed = std::stoi(value);
    if (parsed < 0 || (!allow_zero && parsed == 0)) {
      throw std::invalid_argument(field_name);
    }
    *out_value = static_cast<std::uint32_t>(parsed);
    return true;
  } catch (const std::exception&) {
    if (error_message != nullptr) {
      *error_message = field_name + " 必须是" + (allow_zero ? "非负整数" : "正整数") +
                       ": " + value;
    }
    return false;
  }
}

bool parse_int_field(const std::string& field_name,
                     const std::string& value,
                     bool allow_zero,
                     int* out_value,
                     std::string* error_message) {
  try {
    const int parsed = std::stoi(value);
    if (parsed < 0 || (!allow_zero && parsed == 0)) {
      throw std::invalid_argument(field_name);
    }
    *out_value = parsed;
    return true;
  } catch (const std::exception&) {
    if (error_message != nullptr) {
      *error_message = field_name + " 必须是" + (allow_zero ? "非负整数" : "正整数") +
                       ": " + value;
    }
    return false;
  }
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

bool parse_camera_key(const std::string& key,
                      std::uint32_t* out_camera_index,
                      std::string* out_field) {
  constexpr const char* kPrefix = "camera.";
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
    if (parsed < 0) {
      return false;
    }
    *out_camera_index = static_cast<std::uint32_t>(parsed);
    *out_field = key.substr(field_separator + 1);
    return !out_field->empty();
  } catch (const std::exception&) {
    return false;
  }
}

RuntimeCameraConfig default_camera_config(std::uint32_t camera_index) {
  RuntimeCameraConfig config;
  config.camera_index = camera_index;
  config.camera_id = "CAMERA_" + std::to_string(camera_index);
  return config;
}

RuntimeCameraConfig* ensure_camera(std::map<std::uint32_t, RuntimeCameraConfig>* cameras,
                                   std::uint32_t camera_index) {
  auto iter = cameras->find(camera_index);
  if (iter == cameras->end()) {
    iter = cameras->emplace(camera_index, default_camera_config(camera_index)).first;
  }
  return &iter->second;
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

bool apply_camera_value(RuntimeCameraConfig* camera,
                        const std::string& field,
                        const std::string& value,
                        std::string* error_message) {
  if (field == "camera_id") {
    camera->camera_id = value;
  } else if (field == "serial_number") {
    camera->serial_number = value;
  } else if (field == "width") {
    return parse_uint32_field("camera." + std::to_string(camera->camera_index) + ".width",
                              value,
                              false,
                              &camera->width,
                              error_message);
  } else if (field == "height") {
    return parse_uint32_field("camera." + std::to_string(camera->camera_index) + ".height",
                              value,
                              false,
                              &camera->height,
                              error_message);
  } else if (field == "channels") {
    return parse_uint32_field("camera." + std::to_string(camera->camera_index) + ".channels",
                              value,
                              false,
                              &camera->channels,
                              error_message);
  } else if (field == "pixel_format") {
    camera->pixel_format = value;
  } else if (field == "trigger_line") {
    camera->trigger_line = value;
  } else if (field == "exposure_output_line") {
    camera->exposure_output_line = value;
  } else if (field == "buffer_count") {
    return parse_uint32_field("camera." + std::to_string(camera->camera_index) +
                                  ".buffer_count",
                              value,
                              false,
                              &camera->buffer_count,
                              error_message);
  } else if (field == "simulate_missing_frame") {
    camera->simulate_missing_frame = parse_bool(value);
  } else {
    if (error_message != nullptr) {
      *error_message = "未知相机配置字段: camera." +
                       std::to_string(camera->camera_index) + "." + field;
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
    if (channel.strobe_width_us > channel.exposure_us) {
      if (error_message != nullptr) {
        *error_message = "光源频闪脉宽不能大于曝光时间: light." +
                         std::to_string(light_index);
      }
      return false;
    }
  }
  return true;
}

bool require_non_empty(const std::string& field_name,
                       const std::string& value,
                       std::string* error_message) {
  if (!value.empty() && value.find("TODO") == std::string::npos) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = field_name + " 不能为空，也不能保留 TODO 占位值";
  }
  return false;
}

bool reject_todo_if_set(const std::string& field_name,
                        const std::string& value,
                        std::string* error_message) {
  if (value.find("TODO") == std::string::npos) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = field_name + " 不能保留 TODO 占位值";
  }
  return false;
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
  std::map<std::uint32_t, RuntimeCameraConfig> cameras;
  for (const auto& camera : config.cameras) {
    cameras[camera.camera_index] = camera;
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
    if (key == "hardware_mode") {
      if (!parse_hardware_mode(value, &config.hardware_mode, error_message)) {
        return false;
      }
    } else if (key == "plc.backend") {
      if (!parse_hardware_backend(value, &config.plc.backend, error_message)) {
        return false;
      }
    } else if (key == "camera_backend" || key == "camera.backend") {
      if (!parse_hardware_backend(value, &config.camera_backend, error_message)) {
        return false;
      }
    } else if (key == "light.backend") {
      if (!parse_hardware_backend(value, &config.light.backend, error_message)) {
        return false;
      }
    } else if (key == "plc.host") {
      config.plc.host = value;
    } else if (key == "plc.port") {
      if (!parse_uint32_field("plc.port", value, false, &config.plc.port, error_message)) {
        return false;
      }
    } else if (key == "plc.station_id") {
      config.plc.station_id = value;
    } else if (key == "plc.trigger_source") {
      config.plc.trigger_source = value;
    } else if (key == "plc.trigger_id_source") {
      config.plc.trigger_id_source = value;
    } else if (key == "plc.seat_id_source") {
      config.plc.seat_id_source = value;
    } else if (key == "plc.sku_source") {
      config.plc.sku_source = value;
    } else if (key == "plc.ok_output") {
      config.plc.ok_output = value;
    } else if (key == "plc.ng_output") {
      config.plc.ng_output = value;
    } else if (key == "plc.recheck_output") {
      config.plc.recheck_output = value;
    } else if (key == "plc.ack_input") {
      config.plc.ack_input = value;
    } else if (key == "plc.output_hold_ms") {
      if (!parse_uint32_field("plc.output_hold_ms",
                              value,
                              false,
                              &config.plc.output_hold_ms,
                              error_message)) {
        return false;
      }
    } else if (key == "light.device_id") {
      config.light.device_id = value;
    } else if (key == "light.host") {
      config.light.host = value;
    } else if (key == "light.port") {
      if (!parse_uint32_field("light.port", value, false, &config.light.port, error_message)) {
        return false;
      }
    } else if (key == "light.serial_port") {
      config.light.serial_port = value;
    } else if (key == "light.baud_rate") {
      if (!parse_uint32_field("light.baud_rate",
                              value,
                              false,
                              &config.light.baud_rate,
                              error_message)) {
        return false;
      }
    } else if (key == "light.trigger_input_line") {
      config.light.trigger_input_line = value;
    } else if (key == "slot_count") {
      if (!parse_uint32_field("slot_count", value, false, &config.slot_count, error_message)) {
        return false;
      }
    } else if (key == "frame_slot_size") {
      if (!parse_uint32_field("frame_slot_size",
                              value,
                              false,
                              &config.frame_slot_size,
                              error_message)) {
        return false;
      }
    } else if (key == "result_slot_size") {
      if (!parse_uint32_field("result_slot_size",
                              value,
                              false,
                              &config.result_slot_size,
                              error_message)) {
        return false;
      }
    } else if (key == "detector_timeout_ms") {
      if (!parse_int_field("detector_timeout_ms",
                           value,
                           false,
                           &config.detector_timeout_ms,
                           error_message)) {
        return false;
      }
    } else if (key == "trigger_timeout_ms") {
      if (!parse_int_field("trigger_timeout_ms",
                           value,
                           false,
                           &config.trigger_timeout_ms,
                           error_message)) {
        return false;
      }
    } else if (key == "publish_timeout_ms") {
      if (!parse_int_field("publish_timeout_ms",
                           value,
                           false,
                           &config.publish_timeout_ms,
                           error_message)) {
        return false;
      }
    } else if (key == "camera_timeout_ms") {
      if (!parse_int_field("camera_timeout_ms",
                           value,
                           false,
                           &config.camera_timeout_ms,
                           error_message)) {
        return false;
      }
    } else if (key == "light_timeout_ms") {
      if (!parse_int_field("light_timeout_ms",
                           value,
                           false,
                           &config.light_timeout_ms,
                           error_message)) {
        return false;
      }
    } else if (key == "recipe_id") {
      config.recipe_id = value;
    } else if (key == "max_jobs") {
      if (!parse_int_field("max_jobs", value, true, &config.max_jobs, error_message)) {
        return false;
      }
    } else if (key == "light_order") {
      if (!parse_light_order(value, &config.light_order, error_message)) {
        return false;
      }
    } else if (key == "trigger_sync_mode") {
      if (!parse_trigger_sync_mode(value, &config.trigger_sync_mode, error_message)) {
        return false;
      }
    } else if (key == "acquisition_strategy") {
      if (!parse_acquisition_strategy(value, &config.acquisition_strategy, error_message)) {
        return false;
      }
    } else if (key == "reset_shared_memory") {
      config.reset_shared_memory = parse_bool(value);
    } else if (key == "simulate_light_fault") {
      config.light.simulate_fault = parse_bool(value);
    } else if (key == "light.simulate_fault") {
      config.light.simulate_fault = parse_bool(value);
    } else if (key == "simulate_plc_output_fault") {
      config.plc.simulate_output_fault = parse_bool(value);
    } else if (key == "plc.simulate_output_fault") {
      config.plc.simulate_output_fault = parse_bool(value);
    } else if (key == "simulate_trigger_timeout") {
      config.plc.simulate_trigger_timeout = parse_bool(value);
    } else if (key == "plc.simulate_trigger_timeout") {
      config.plc.simulate_trigger_timeout = parse_bool(value);
    } else if (key == "simulate_missing_frame") {
      for (auto& camera : config.cameras) {
        camera.simulate_missing_frame = parse_bool(value);
      }
      for (auto& [camera_index, camera] : cameras) {
        (void)camera_index;
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
      } else {
        std::uint32_t camera_index = 0;
        std::string camera_field;
        if (parse_camera_key(key, &camera_index, &camera_field)) {
          auto* camera = ensure_camera(&cameras, camera_index);
          if (!apply_camera_value(camera, camera_field, value, error_message)) {
            return false;
          }
        } else {
          if (error_message != nullptr) {
            *error_message = "未知运行配置字段: " + key;
          }
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
  config.cameras.clear();
  for (const auto& [camera_index, camera] : cameras) {
    (void)camera_index;
    config.cameras.push_back(camera);
  }
  if (!validate_station_runtime_config(config, error_message)) {
    return false;
  }
  *out_config = config;
  return true;
}

bool validate_station_runtime_config(const StationRuntimeConfig& config,
                                     std::string* error_message) {
  if (config.acquisition_strategy != AcquisitionStrategy::SerialTdm) {
    if (error_message != nullptr) {
      *error_message = "当前只允许 serial_tdm 采集策略";
    }
    return false;
  }
  if (config.slot_count == 0) {
    if (error_message != nullptr) {
      *error_message = "slot_count 必须大于 0";
    }
    return false;
  }
  if (config.frame_slot_size <= sizeof(FrameSlotHeader) ||
      config.result_slot_size <= sizeof(ResultSlotHeader)) {
    if (error_message != nullptr) {
      *error_message = "frame_slot_size/result_slot_size 太小，无法容纳共享内存槽位头";
    }
    return false;
  }
  if (config.detector_timeout_ms <= 0 || config.trigger_timeout_ms <= 0 ||
      config.publish_timeout_ms <= 0 || config.camera_timeout_ms <= 0 ||
      config.light_timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "所有 timeout_ms 配置都必须大于 0";
    }
    return false;
  }
  if (config.cameras.empty()) {
    if (error_message != nullptr) {
      *error_message = "至少需要配置 1 台相机";
    }
    return false;
  }
  std::map<std::uint32_t, bool> camera_indices;
  for (const auto& camera : config.cameras) {
    if (camera_indices[camera.camera_index]) {
      if (error_message != nullptr) {
        *error_message = "相机 camera_index 重复: " + std::to_string(camera.camera_index);
      }
      return false;
    }
    camera_indices[camera.camera_index] = true;
    if (camera.width == 0 || camera.height == 0 || camera.channels == 0 ||
        camera.buffer_count == 0) {
      if (error_message != nullptr) {
        *error_message = "相机尺寸、通道数和 buffer_count 必须大于 0: camera." +
                         std::to_string(camera.camera_index);
      }
      return false;
    }
  }
  const std::uint64_t expected_frame_count =
      static_cast<std::uint64_t>(config.cameras.size()) * config.light_order.size();
  if (expected_frame_count == 0 || expected_frame_count > kMaxFramesPerJob) {
    if (error_message != nullptr) {
      *error_message = "相机数量 x 光源数量超过单任务最大帧数或为空";
    }
    return false;
  }
  std::map<std::uint32_t, RuntimeLightChannelConfig> configured_light_channels;
  for (const auto& channel : config.light_channels) {
    if (channel.light_index == 0 ||
        configured_light_channels[channel.light_index].light_index != 0) {
      if (error_message != nullptr) {
        *error_message = "光源 light_index 为空或重复";
      }
      return false;
    }
    configured_light_channels[channel.light_index] = channel;
  }
  if (!validate_light_channels(config.light_order, configured_light_channels, error_message)) {
    return false;
  }
  std::uint64_t estimated_payload_size =
      frame_slot_image_offset(static_cast<std::uint32_t>(expected_frame_count));
  for (const auto& camera : config.cameras) {
    estimated_payload_size += static_cast<std::uint64_t>(camera.width) *
                              camera.height * camera.channels *
                              config.light_order.size();
  }
  if (estimated_payload_size > config.frame_slot_size) {
    if (error_message != nullptr) {
      *error_message = "frame_slot_size 太小，无法容纳串行 TDM 采集图像包";
    }
    return false;
  }

  if (config.hardware_mode == HardwareMode::Simulated) {
    if (!is_simulated_backend(config.plc.backend) ||
        !is_simulated_backend(config.camera_backend) ||
        !is_simulated_backend(config.light.backend)) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=simulated 时 plc.backend/camera_backend/"
                         "light.backend 必须都是 simulated";
      }
      return false;
    }
    return true;
  }

  if (config.trigger_sync_mode != TriggerSyncMode::CameraExposureOutput) {
    if (error_message != nullptr) {
      *error_message = "生产模式必须使用 camera_exposure_output 或等价硬触发同步";
    }
    return false;
  }

  if (is_simulated_backend(config.plc.backend) ||
      is_simulated_backend(config.camera_backend) ||
      is_simulated_backend(config.light.backend)) {
    if (error_message != nullptr) {
      *error_message = "hardware_mode=production 时不能使用 simulated backend；"
                       "请填写 plc.backend、camera_backend 和 light.backend";
    }
    return false;
  }

  if (!reject_todo_if_set("plc.host", config.plc.host, error_message) ||
      !reject_todo_if_set("plc.station_id", config.plc.station_id, error_message) ||
      !reject_todo_if_set("plc.ack_input", config.plc.ack_input, error_message) ||
      !reject_todo_if_set("light.device_id", config.light.device_id, error_message) ||
      !reject_todo_if_set("light.host", config.light.host, error_message) ||
      !reject_todo_if_set("light.serial_port", config.light.serial_port, error_message)) {
    return false;
  }

  if (!require_non_empty("plc.trigger_source", config.plc.trigger_source, error_message) ||
      !require_non_empty("plc.trigger_id_source", config.plc.trigger_id_source, error_message) ||
      !require_non_empty("plc.seat_id_source", config.plc.seat_id_source, error_message) ||
      !require_non_empty("plc.sku_source", config.plc.sku_source, error_message) ||
      !require_non_empty("plc.ok_output", config.plc.ok_output, error_message) ||
      !require_non_empty("plc.ng_output", config.plc.ng_output, error_message) ||
      !require_non_empty("plc.recheck_output", config.plc.recheck_output, error_message)) {
    return false;
  }
  if ((config.plc.backend == HardwareBackend::ModbusTcp ||
       config.plc.backend == HardwareBackend::SiemensS7) &&
      !require_non_empty("plc.host", config.plc.host, error_message)) {
    return false;
  }
  if ((config.plc.backend == HardwareBackend::ModbusTcp ||
       config.plc.backend == HardwareBackend::SiemensS7) &&
      config.plc.port == 0) {
    if (error_message != nullptr) {
      *error_message = "plc.port 必须大于 0";
    }
    return false;
  }
  for (const auto& camera : config.cameras) {
    const std::string prefix = "camera." + std::to_string(camera.camera_index);
    if (!reject_todo_if_set(prefix + ".camera_id", camera.camera_id, error_message) ||
        !reject_todo_if_set(prefix + ".pixel_format", camera.pixel_format, error_message)) {
      return false;
    }
    if (!require_non_empty(prefix + ".camera_id", camera.camera_id, error_message) ||
        !require_non_empty(prefix + ".serial_number", camera.serial_number, error_message) ||
        !require_non_empty(prefix + ".pixel_format", camera.pixel_format, error_message) ||
        !require_non_empty(prefix + ".trigger_line", camera.trigger_line, error_message) ||
        !require_non_empty(prefix + ".exposure_output_line",
                           camera.exposure_output_line,
                           error_message)) {
      return false;
    }
  }
  if (config.light.backend == HardwareBackend::SerialAscii) {
    if (!require_non_empty("light.serial_port", config.light.serial_port, error_message)) {
      return false;
    }
    if (config.light.baud_rate == 0) {
      if (error_message != nullptr) {
        *error_message = "light.baud_rate 必须大于 0";
      }
      return false;
    }
  }
  if ((config.light.backend == HardwareBackend::ModbusTcp ||
       config.light.backend == HardwareBackend::VendorSdk ||
       config.light.backend == HardwareBackend::CustomSdk) &&
      config.light.host.empty() && config.light.serial_port.empty() &&
      config.light.device_id.empty()) {
    if (error_message != nullptr) {
      *error_message = "频闪控制器至少需要填写 light.host、light.serial_port 或 light.device_id";
    }
    return false;
  }
  if (!require_non_empty("light.trigger_input_line",
                         config.light.trigger_input_line,
                         error_message)) {
    return false;
  }
  return true;
}

const char* acquisition_strategy_name(AcquisitionStrategy strategy) {
  switch (strategy) {
    case AcquisitionStrategy::SerialTdm:
      return "serial_tdm";
  }
  return "unknown";
}

}  // namespace seat_aoi
