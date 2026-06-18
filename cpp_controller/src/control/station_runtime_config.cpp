#include "control/station_runtime_config.hpp"

#include <exception>
#include <fstream>
#include <map>
#include <sstream>
#include <stdexcept>
#include <string>

namespace seat_aoi {

namespace {

bool parse_bool_field(const std::string& field_name,
                      const std::string& value,
                      bool* out_value,
                      std::string* error_message) {
  if (value == "true" || value == "1" || value == "yes" || value == "on") {
    *out_value = true;
    return true;
  }
  if (value == "false" || value == "0" || value == "no" || value == "off") {
    *out_value = false;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = field_name + " 必须是布尔值 true/false/1/0/yes/no/on/off: " + value;
  }
  return false;
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

bool parse_capture_mode_value(const std::string& value,
                              CaptureMode* out_mode,
                              std::string* error_message) {
  if (value == "fixed_camera" || value == "fixed" || value == "stationary") {
    *out_mode = CaptureMode::FixedCamera;
    return true;
  }
  if (value == "robot_flyshot" || value == "robot" || value == "flyshot") {
    *out_mode = CaptureMode::RobotFlyshot;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "capture_mode 只能是 fixed_camera 或 robot_flyshot: " + value;
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

bool parse_uint64_field(const std::string& field_name,
                        const std::string& value,
                        bool allow_zero,
                        std::uint64_t* out_value,
                        std::string* error_message) {
  try {
    if (!value.empty() && value.front() == '-') {
      throw std::invalid_argument(field_name);
    }
    const unsigned long long parsed = std::stoull(value);
    if (!allow_zero && parsed == 0ULL) {
      throw std::invalid_argument(field_name);
    }
    *out_value = static_cast<std::uint64_t>(parsed);
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

bool parse_pose_key(const std::string& key,
                    std::uint32_t* out_pose_index,
                    std::string* out_field) {
  constexpr const char* kPrefix = "pose.";
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
    *out_pose_index = static_cast<std::uint32_t>(parsed);
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

RuntimeCaptureViewConfig default_capture_view_config(std::uint32_t pose_index) {
  RuntimeCaptureViewConfig config;
  config.pose_index = pose_index;
  config.pose_id = "POSE_" + std::to_string(pose_index);
  config.camera_index = 0;
  config.camera_id = "TOP_BACK";
  return config;
}

RuntimeCaptureViewConfig* ensure_capture_view(
    std::map<std::uint32_t, RuntimeCaptureViewConfig>* views,
    std::uint32_t pose_index) {
  auto iter = views->find(pose_index);
  if (iter == views->end()) {
    iter = views->emplace(pose_index, default_capture_view_config(pose_index)).first;
  }
  return &iter->second;
}

bool parse_float3(const std::string& field_name,
                  const std::string& value,
                  float* out_values,
                  std::string* error_message) {
  std::stringstream stream(value);
  std::string item;
  int index = 0;
  while (std::getline(stream, item, ',')) {
    item = trim(item);
    if (item.empty()) {
      continue;
    }
    if (index >= 3) {
      if (error_message != nullptr) {
        *error_message = field_name + " 必须包含 3 个数字: " + value;
      }
      return false;
    }
    try {
      out_values[index] = std::stof(item);
    } catch (const std::exception&) {
      if (error_message != nullptr) {
        *error_message = field_name + " 解析失败: " + value;
      }
      return false;
    }
    ++index;
  }
  if (index != 3) {
    if (error_message != nullptr) {
      *error_message = field_name + " 必须包含 3 个数字: " + value;
    }
    return false;
  }
  return true;
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
  } else if (field == "calibration_id") {
    camera->calibration_id = value;
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
    return parse_bool_field("camera." + std::to_string(camera->camera_index) +
                                ".simulate_missing_frame",
                            value,
                            &camera->simulate_missing_frame,
                            error_message);
  } else {
    if (error_message != nullptr) {
      *error_message = "未知相机配置字段: camera." +
                       std::to_string(camera->camera_index) + "." + field;
    }
    return false;
  }
  return true;
}

bool apply_capture_view_value(RuntimeCaptureViewConfig* view,
                              const std::string& field,
                              const std::string& value,
                              std::string* error_message) {
  if (field == "pose_id") {
    view->pose_id = value;
  } else if (field == "camera_index") {
    return parse_uint32_field("pose." + std::to_string(view->pose_index) + ".camera_index",
                              value,
                              true,
                              &view->camera_index,
                              error_message);
  } else if (field == "camera_id") {
    view->camera_id = value;
  } else if (field == "calibration_id") {
    view->calibration_id = value;
  } else if (field == "shot_id_source") {
    view->shot_id_source = value;
  } else if (field == "robot_ready_input") {
    view->robot_ready_input = value;
  } else if (field == "robot_fault_input") {
    view->robot_fault_input = value;
  } else if (field == "photo_trigger_input") {
    view->photo_trigger_input = value;
  } else if (field == "simulated_shot_id") {
    return parse_uint64_field("pose." + std::to_string(view->pose_index) + ".simulated_shot_id",
                              value,
                              true,
                              &view->simulated_shot_id,
                              error_message);
  } else if (field == "robot_tcp_xyz_mm") {
    return parse_float3("pose." + std::to_string(view->pose_index) + ".robot_tcp_xyz_mm",
                        value,
                        view->robot_tcp_xyz_mm,
                        error_message);
  } else if (field == "robot_rpy_deg") {
    return parse_float3("pose." + std::to_string(view->pose_index) + ".robot_rpy_deg",
                        value,
                        view->robot_rpy_deg,
                        error_message);
  } else {
    if (error_message != nullptr) {
      *error_message = "未知 pose 配置字段: pose." +
                       std::to_string(view->pose_index) + "." + field;
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

bool validate_capture_views(const StationRuntimeConfig& config,
                            std::string* error_message) {
  std::map<std::uint32_t, RuntimeCameraConfig> cameras;
  for (const auto& camera : config.cameras) {
    cameras[camera.camera_index] = camera;
  }

  if (config.capture_mode == CaptureMode::RobotFlyshot && config.capture_views.empty()) {
    if (error_message != nullptr) {
      *error_message = "capture_mode=robot_flyshot 时必须配置 pose.<N> 采集计划";
    }
    return false;
  }

  std::map<std::uint32_t, bool> pose_indices;
  std::map<std::string, bool> pose_ids;
  for (const auto& view : config.capture_views) {
    if (pose_indices[view.pose_index]) {
      if (error_message != nullptr) {
        *error_message = "pose_index 重复: " + std::to_string(view.pose_index);
      }
      return false;
    }
    pose_indices[view.pose_index] = true;
    if (view.pose_id.empty()) {
      if (error_message != nullptr) {
        *error_message = "pose." + std::to_string(view.pose_index) + ".pose_id 不能为空";
      }
      return false;
    }
    if (pose_ids[view.pose_id]) {
      if (error_message != nullptr) {
        *error_message = "pose_id 重复: " + view.pose_id;
      }
      return false;
    }
    pose_ids[view.pose_id] = true;
    const auto camera_iter = cameras.find(view.camera_index);
    if (camera_iter == cameras.end()) {
      if (error_message != nullptr) {
        *error_message = "pose." + std::to_string(view.pose_index) +
                         ".camera_index 未配置相机: " +
                         std::to_string(view.camera_index);
      }
      return false;
    }
    if (!view.camera_id.empty() && view.camera_id != camera_iter->second.camera_id) {
      if (error_message != nullptr) {
        *error_message = "pose." + std::to_string(view.pose_index) +
                         ".camera_id 与 camera_index 对应相机不一致: " +
                         view.camera_id + " != " + camera_iter->second.camera_id;
      }
      return false;
    }
    if (view.calibration_id.empty() || view.calibration_id.find("TODO") != std::string::npos) {
      if (error_message != nullptr) {
        *error_message = "pose." + std::to_string(view.pose_index) +
                         ".calibration_id 不能为空，也不能保留 TODO 占位值";
      }
      return false;
    }
  }
  return true;
}

std::uint64_t effective_view_count(const StationRuntimeConfig& config) {
  if (!config.capture_views.empty()) {
    return config.capture_views.size();
  }
  return config.cameras.size();
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

std::uint32_t bytes_per_channel_for_pixel_format(const std::string& pixel_format) {
  if (pixel_format == "Mono8" || pixel_format == "BGR8" || pixel_format == "RGB8" ||
      pixel_format == "BayerRG8") {
    return 1;
  }
  if (pixel_format == "Mono10" || pixel_format == "Mono12" || pixel_format == "Mono16" ||
      pixel_format == "BayerRG12") {
    return 2;
  }
  return 0;
}

}  // namespace

const char* capture_mode_name(CaptureMode mode) {
  switch (mode) {
    case CaptureMode::FixedCamera:
      return "fixed_camera";
    case CaptureMode::RobotFlyshot:
      return "robot_flyshot";
  }
  return "unknown";
}

bool parse_capture_mode(const std::string& value,
                        CaptureMode* out_mode,
                        std::string* error_message) {
  return parse_capture_mode_value(value, out_mode, error_message);
}

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
  bool has_explicit_camera_config = false;
  std::map<std::uint32_t, RuntimeCaptureViewConfig> capture_views;
  for (const auto& view : config.capture_views) {
    capture_views[view.pose_index] = view;
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
    } else if (key == "signal.backend" || key == "plc.backend") {
      if (!parse_hardware_backend(value, &config.signal.backend, error_message)) {
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
    } else if (key == "robot.backend") {
      if (!parse_hardware_backend(value, &config.robot.backend, error_message)) {
        return false;
      }
    } else if (key == "robot.controller_id") {
      config.robot.controller_id = value;
    } else if (key == "robot.host") {
      config.robot.host = value;
    } else if (key == "robot.port") {
      if (!parse_uint32_field("robot.port", value, false, &config.robot.port, error_message)) {
        return false;
      }
    } else if (key == "robot.ready_input") {
      config.robot.ready_input = value;
    } else if (key == "robot.fault_input") {
      config.robot.fault_input = value;
    } else if (key == "robot.start_output") {
      config.robot.start_output = value;
    } else if (key == "signal.station_id" || key == "plc.station_id") {
      config.signal.station_id = value;
    } else if (key == "signal.default_seat_id") {
      config.signal.default_seat_id = value;
    } else if (key == "signal.default_sku" || key == "plc.sku_source") {
      config.signal.default_sku = value;
    } else if (key == "signal.trigger_queue_path" || key == "signal.trigger_queue") {
      config.signal.trigger_queue_path = value;
    } else if (key == "signal.result_queue_path" || key == "signal.result_queue") {
      config.signal.result_queue_path = value;
    } else if (key == "plc.host" || key == "plc.port" ||
               key == "plc.trigger_source" || key == "plc.trigger_id_source" ||
               key == "plc.seat_id_source" || key == "plc.ok_output" ||
               key == "plc.ng_output" || key == "plc.recheck_output" ||
               key == "plc.ack_input" || key == "plc.output_hold_ms") {
      if (error_message != nullptr) {
        *error_message = key + " 已移除；C++ 只接收外部归一化信号，请改用 signal.* 配置";
      }
      return false;
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
    } else if (key == "warning_recheck_threshold") {
      if (!parse_uint32_field("warning_recheck_threshold",
                              value,
                              false,
                              &config.warning_recheck_threshold,
                              error_message)) {
        return false;
      }
    } else if (key == "critical_recheck_threshold") {
      if (!parse_uint32_field("critical_recheck_threshold",
                              value,
                              false,
                              &config.critical_recheck_threshold,
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
    } else if (key == "capture_mode") {
      if (!parse_capture_mode_value(value, &config.capture_mode, error_message)) {
        return false;
      }
    } else if (key == "trigger_sync_mode") {
      if (!parse_trigger_sync_mode(value, &config.trigger_sync_mode, error_message)) {
        return false;
      }
    } else if (key == "reset_shared_memory") {
      if (!parse_bool_field(key, value, &config.reset_shared_memory, error_message)) {
        return false;
      }
    } else if (key == "simulate_light_fault") {
      if (!parse_bool_field(key, value, &config.light.simulate_fault, error_message)) {
        return false;
      }
    } else if (key == "light.simulate_fault") {
      if (!parse_bool_field(key, value, &config.light.simulate_fault, error_message)) {
        return false;
      }
    } else if (key == "simulate_signal_result_fault" ||
               key == "simulate_plc_output_fault") {
      if (!parse_bool_field(key, value, &config.signal.simulate_output_fault, error_message)) {
        return false;
      }
    } else if (key == "signal.simulate_output_fault" ||
               key == "plc.simulate_output_fault") {
      if (!parse_bool_field(key, value, &config.signal.simulate_output_fault, error_message)) {
        return false;
      }
    } else if (key == "simulate_trigger_timeout") {
      if (!parse_bool_field(key, value, &config.signal.simulate_trigger_timeout, error_message)) {
        return false;
      }
    } else if (key == "signal.simulate_trigger_timeout" ||
               key == "plc.simulate_trigger_timeout") {
      if (!parse_bool_field(key, value, &config.signal.simulate_trigger_timeout, error_message)) {
        return false;
      }
    } else if (key == "simulate_robot_fault") {
      if (!parse_bool_field(key, value, &config.robot.simulate_fault, error_message)) {
        return false;
      }
    } else if (key == "robot.simulate_fault") {
      if (!parse_bool_field(key, value, &config.robot.simulate_fault, error_message)) {
        return false;
      }
    } else if (key == "simulate_missing_frame") {
      bool simulate_missing_frame = false;
      if (!parse_bool_field(key, value, &simulate_missing_frame, error_message)) {
        return false;
      }
      for (auto& camera : config.cameras) {
        camera.simulate_missing_frame = simulate_missing_frame;
      }
      for (auto& [camera_index, camera] : cameras) {
        (void)camera_index;
        camera.simulate_missing_frame = simulate_missing_frame;
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
          if (!has_explicit_camera_config) {
            cameras.clear();
            has_explicit_camera_config = true;
          }
          auto* camera = ensure_camera(&cameras, camera_index);
          if (!apply_camera_value(camera, camera_field, value, error_message)) {
            return false;
          }
        } else {
          std::uint32_t pose_index = 0;
          std::string pose_field;
          if (parse_pose_key(key, &pose_index, &pose_field)) {
            auto* view = ensure_capture_view(&capture_views, pose_index);
            if (!apply_capture_view_value(view, pose_field, value, error_message)) {
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
  config.capture_views.clear();
  for (const auto& [pose_index, view] : capture_views) {
    (void)pose_index;
    config.capture_views.push_back(view);
  }
  if (!validate_station_runtime_config(config, error_message)) {
    return false;
  }
  *out_config = config;
  return true;
}

bool validate_station_runtime_config(const StationRuntimeConfig& config,
                                     std::string* error_message) {
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
  if (config.warning_recheck_threshold == 0 ||
      config.critical_recheck_threshold <= config.warning_recheck_threshold) {
    if (error_message != nullptr) {
      *error_message = "critical_recheck_threshold 必须大于 warning_recheck_threshold";
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
    if (bytes_per_channel_for_pixel_format(camera.pixel_format) == 0) {
      if (error_message != nullptr) {
        *error_message = "不支持的相机 pixel_format: camera." +
                         std::to_string(camera.camera_index) + "." + camera.pixel_format;
      }
      return false;
    }
  }
  if (!validate_capture_views(config, error_message)) {
    return false;
  }
  const std::uint64_t expected_frame_count =
      effective_view_count(config) * config.light_order.size();
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
  std::map<std::uint32_t, RuntimeCameraConfig> camera_by_index;
  for (const auto& camera : config.cameras) {
    camera_by_index[camera.camera_index] = camera;
  }
  std::uint64_t estimated_payload_size =
      frame_slot_image_offset(static_cast<std::uint32_t>(expected_frame_count));
  if (config.capture_views.empty()) {
    for (const auto& camera : config.cameras) {
      const std::uint32_t bytes_per_channel =
          bytes_per_channel_for_pixel_format(camera.pixel_format);
      estimated_payload_size += static_cast<std::uint64_t>(camera.width) *
                                camera.height * camera.channels * bytes_per_channel *
                                config.light_order.size();
    }
  } else {
    for (const auto& view : config.capture_views) {
      const auto camera_iter = camera_by_index.find(view.camera_index);
      if (camera_iter == camera_by_index.end()) {
        continue;
      }
      const auto& camera = camera_iter->second;
      const std::uint32_t bytes_per_channel =
          bytes_per_channel_for_pixel_format(camera.pixel_format);
      estimated_payload_size += static_cast<std::uint64_t>(camera.width) *
                                camera.height * camera.channels * bytes_per_channel *
                                config.light_order.size();
    }
  }
  if (estimated_payload_size > config.frame_slot_size) {
    if (error_message != nullptr) {
      *error_message = "frame_slot_size 太小，无法容纳串行 TDM 采集图像包";
    }
    return false;
  }

  const bool signal_is_simulated = is_simulated_backend(config.signal.backend);
  const bool signal_is_manual = is_manual_trigger_backend(config.signal.backend);
  const bool signal_is_external = is_external_signal_backend(config.signal.backend);
  const bool camera_is_simulated = is_simulated_backend(config.camera_backend);
  const bool light_is_simulated = is_simulated_backend(config.light.backend);
  const bool robot_is_simulated = is_simulated_backend(config.robot.backend);

  if (config.hardware_mode == HardwareMode::Simulated) {
    if (!is_simulated_backend(config.signal.backend) ||
        !is_simulated_backend(config.camera_backend) ||
        !is_simulated_backend(config.light.backend) ||
        !is_simulated_backend(config.robot.backend)) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=simulated 时 signal.backend/camera_backend/"
                         "light.backend/robot.backend 必须都是 simulated";
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

  if (config.hardware_mode == HardwareMode::Lab) {
    if (!signal_is_manual && !signal_is_simulated && !signal_is_external) {
      if (error_message != nullptr) {
        *error_message =
            "hardware_mode=lab 时 signal.backend 只能是 manual_trigger、external_signal 或 simulated";
      }
      return false;
    }
    if (config.capture_mode == CaptureMode::RobotFlyshot && robot_is_simulated) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=lab 的机器人飞拍模式必须配置真实 robot.backend";
      }
      return false;
    }
  } else if (signal_is_simulated || signal_is_manual || !signal_is_external ||
             camera_is_simulated ||
             light_is_simulated ||
             (config.capture_mode == CaptureMode::RobotFlyshot && robot_is_simulated)) {
    if (error_message != nullptr) {
      *error_message = "hardware_mode=production 时 signal.backend 必须是 external_signal，"
                       "且不能使用 simulated/manual_trigger/camera/light backend；"
                       "请填写 signal.backend、camera_backend、light.backend 和 robot.backend";
    }
    return false;
  }

  if (!reject_todo_if_set("signal.station_id", config.signal.station_id, error_message) ||
      !reject_todo_if_set("signal.default_seat_id",
                          config.signal.default_seat_id,
                          error_message) ||
      !reject_todo_if_set("signal.default_sku", config.signal.default_sku, error_message) ||
      !reject_todo_if_set("light.device_id", config.light.device_id, error_message) ||
      !reject_todo_if_set("light.host", config.light.host, error_message) ||
      !reject_todo_if_set("light.serial_port", config.light.serial_port, error_message)) {
    return false;
  }

  if (!signal_is_manual &&
      (!require_non_empty("signal.station_id", config.signal.station_id, error_message) ||
       !require_non_empty("signal.default_sku", config.signal.default_sku, error_message))) {
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
  if (config.capture_mode == CaptureMode::RobotFlyshot) {
    if (!reject_todo_if_set("robot.controller_id", config.robot.controller_id, error_message) ||
        !reject_todo_if_set("robot.host", config.robot.host, error_message) ||
        !reject_todo_if_set("robot.ready_input", config.robot.ready_input, error_message) ||
        !reject_todo_if_set("robot.fault_input", config.robot.fault_input, error_message) ||
        !reject_todo_if_set("robot.start_output", config.robot.start_output, error_message)) {
      return false;
    }
    if (!require_non_empty("robot.controller_id", config.robot.controller_id, error_message) ||
        !require_non_empty("robot.ready_input", config.robot.ready_input, error_message) ||
        !require_non_empty("robot.fault_input", config.robot.fault_input, error_message) ||
        !require_non_empty("robot.start_output", config.robot.start_output, error_message)) {
      return false;
    }
    if ((config.robot.backend == HardwareBackend::ModbusTcp ||
         config.robot.backend == HardwareBackend::SiemensS7 ||
         config.robot.backend == HardwareBackend::VendorSdk ||
         config.robot.backend == HardwareBackend::CustomSdk) &&
        config.robot.host.empty()) {
      if (error_message != nullptr) {
        *error_message = "robot.host 不能为空";
      }
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

}  // namespace seat_aoi
