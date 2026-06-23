#include "control/station_runtime_config.hpp"

#include <exception>
#include <fstream>
#include <map>
#include <sstream>
#include <string>

#include "common/string_utils.hpp"

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
    *error_message = field_name + " 必须是布尔值: " + value;
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
      *error_message = field_name + " 必须是" +
                       std::string(allow_zero ? "非负整数" : "正整数") + ": " + value;
    }
    return false;
  }
}

bool parse_int_field(const std::string& field_name,
                     const std::string& value,
                     bool allow_zero,
                     int* out_value,
                     std::string* error_message) {
  std::uint32_t parsed = 0;
  if (!parse_uint32_field(field_name, value, allow_zero, &parsed, error_message)) {
    return false;
  }
  *out_value = static_cast<int>(parsed);
  return true;
}

bool parse_float_field(const std::string& field_name,
                       const std::string& value,
                       float min_value,
                       float max_value,
                       float* out_value,
                       std::string* error_message) {
  try {
    const float parsed = std::stof(value);
    if (parsed < min_value || parsed > max_value) {
      throw std::invalid_argument(field_name);
    }
    *out_value = parsed;
    return true;
  } catch (const std::exception&) {
    if (error_message != nullptr) {
      *error_message = field_name + " 必须在 " + std::to_string(min_value) + " 到 " +
                       std::to_string(max_value) + " 之间: " + value;
    }
    return false;
  }
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
        throw std::invalid_argument("light_order");
      }
      light_order.push_back(static_cast<std::uint32_t>(parsed));
    } catch (const std::exception&) {
      if (error_message != nullptr) {
        *error_message = "light_order 只能包含正整数: " + value;
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

bool parse_light_serial_response_mode_value(const std::string& value,
                                            LightSerialResponseMode* out_mode,
                                            std::string* error_message) {
  if (value == "ack" || value == "strict_ack" || value == "strict") {
    *out_mode = LightSerialResponseMode::Ack;
    return true;
  }
  if (value == "none" || value == "no_ack" || value == "write_only") {
    *out_mode = LightSerialResponseMode::None;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "light.response_mode 只能是 ack 或 none: " + value;
  }
  return false;
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
  const auto index_text = key.substr(after_prefix, field_separator - after_prefix);
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
  const auto index_text = key.substr(after_prefix, field_separator - after_prefix);
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

RuntimeLightChannelConfig default_light_channel_config(std::uint32_t light_index) {
  RuntimeLightChannelConfig config;
  config.light_index = light_index;
  config.physical_channel = light_index;
  return config;
}

RuntimeCameraConfig default_camera_config(std::uint32_t camera_index) {
  RuntimeCameraConfig config;
  config.camera_index = camera_index;
  config.camera_id = "CAMERA_" + std::to_string(camera_index);
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
  if (field == "physical_channel") {
    return parse_uint32_field("light." + std::to_string(channel->light_index) +
                                  ".physical_channel",
                              value,
                              false,
                              &channel->physical_channel,
                              error_message);
  }
  if (field == "exposure_us") {
    return parse_uint32_field("light." + std::to_string(channel->light_index) +
                                  ".exposure_us",
                              value,
                              false,
                              &channel->exposure_us,
                              error_message);
  }
  if (field == "strobe_width_us") {
    return parse_uint32_field("light." + std::to_string(channel->light_index) +
                                  ".strobe_width_us",
                              value,
                              false,
                              &channel->strobe_width_us,
                              error_message);
  }
  if (field == "trigger_delay_us") {
    return parse_uint32_field("light." + std::to_string(channel->light_index) +
                                  ".trigger_delay_us",
                              value,
                              true,
                              &channel->trigger_delay_us,
                              error_message);
  }
  if (field == "gain") {
    return parse_float_field("light." + std::to_string(channel->light_index) + ".gain",
                             value,
                             0.01F,
                             100.0F,
                             &channel->gain,
                             error_message);
  }
  if (field == "current_percent") {
    return parse_float_field("light." + std::to_string(channel->light_index) +
                                 ".current_percent",
                             value,
                             0.01F,
                             100.0F,
                             &channel->current_percent,
                             error_message);
  }
  if (field == "acquisition_mode") {
    if (value == "strobe" || value == "flash") {
      channel->acquisition_mode = LightAcquisitionMode::Strobe;
      return true;
    }
    if (error_message != nullptr) {
      *error_message = "当前频闪链路只允许 light.<N>.acquisition_mode=strobe";
    }
    return false;
  }
  if (error_message != nullptr) {
    *error_message = "未知光源字段: light." + std::to_string(channel->light_index) +
                     "." + field;
  }
  return false;
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
      *error_message = "未知相机字段: camera." + std::to_string(camera->camera_index) +
                       "." + field;
    }
    return false;
  }
  return true;
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

bool validate_light_channels(const StationRuntimeConfig& config,
                             std::string* error_message) {
  if (config.lights.size() != 1) {
    if (error_message != nullptr) {
      *error_message = "当前真实链路只支持 1 台 FL-ACDH 频闪控制器";
    }
    return false;
  }
  if (config.light_order.empty()) {
    if (error_message != nullptr) {
      *error_message = "light_order 不能为空";
    }
    return false;
  }
  std::map<std::uint32_t, RuntimeLightChannelConfig> channels;
  for (const auto& channel : config.light_channels) {
    if (channel.light_index == 0 || channels[channel.light_index].light_index != 0) {
      if (error_message != nullptr) {
        *error_message = "光源 light_index 为空或重复";
      }
      return false;
    }
    channels[channel.light_index] = channel;
  }
  for (std::uint32_t light_index : config.light_order) {
    const auto iter = channels.find(light_index);
    if (iter == channels.end()) {
      if (error_message != nullptr) {
        *error_message = "light_order 中的光源缺少配置: light." +
                         std::to_string(light_index);
      }
      return false;
    }
    const auto& channel = iter->second;
    if (channel.controller_index != 0 ||
        channel.acquisition_mode != LightAcquisitionMode::Strobe ||
        channel.physical_channel == 0 ||
        channel.exposure_us == 0 ||
        channel.strobe_width_us < 10 ||
        channel.strobe_width_us > 999 ||
        channel.strobe_width_us > channel.exposure_us ||
        channel.trigger_delay_us < 5 ||
        channel.trigger_delay_us > 99 ||
        channel.gain <= 0.0F ||
        channel.current_percent <= 0.0F ||
        channel.current_percent > 100.0F) {
      if (error_message != nullptr) {
        *error_message = "光源配置非法: light." + std::to_string(light_index);
      }
      return false;
    }
  }
  return true;
}

std::uint64_t estimated_payload_size(const StationRuntimeConfig& config) {
  const std::uint64_t expected_frame_count = config.cameras.size() * config.light_order.size();
  std::uint64_t payload_size =
      frame_slot_image_offset(static_cast<std::uint32_t>(expected_frame_count));
  for (const auto& camera : config.cameras) {
    const std::uint32_t bytes_per_channel =
        bytes_per_channel_for_pixel_format(camera.pixel_format);
    payload_size += static_cast<std::uint64_t>(camera.width) *
                    camera.height * camera.channels * bytes_per_channel *
                    config.light_order.size();
  }
  return payload_size;
}

}  // namespace

const char* controller_mode_name(ControllerMode mode) {
  switch (mode) {
    case ControllerMode::Online:
      return "online";
    case ControllerMode::CaptureOnly:
      return "capture_only";
  }
  return "unknown";
}

bool parse_controller_mode(const std::string& value,
                           ControllerMode* out_mode,
                           std::string* error_message) {
  if (value == "online" || value == "inspection" || value == "detect") {
    *out_mode = ControllerMode::Online;
    return true;
  }
  if (value == "capture_only" || value == "capture" || value == "acquire_only") {
    *out_mode = ControllerMode::CaptureOnly;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "controller_mode 只能是 online 或 capture_only: " + value;
  }
  return false;
}

const char* capture_mode_name(CaptureMode mode) {
  switch (mode) {
    case CaptureMode::FixedCamera:
      return "fixed_camera";
  }
  return "unknown";
}

const char* capture_schedule_name(CaptureSchedule schedule) {
  switch (schedule) {
    case CaptureSchedule::SharedLightParallel:
      return "shared_light_parallel";
  }
  return "unknown";
}

bool parse_capture_mode(const std::string& value,
                        CaptureMode* out_mode,
                        std::string* error_message) {
  if (value == "fixed_camera" || value == "fixed" || value == "stationary") {
    *out_mode = CaptureMode::FixedCamera;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "当前真实链路只支持 capture_mode=fixed_camera: " + value;
  }
  return false;
}

bool parse_capture_schedule(const std::string& value,
                            CaptureSchedule* out_schedule,
                            std::string* error_message) {
  if (value == "shared_light_parallel" || value == "light_parallel" ||
      value == "parallel_light") {
    *out_schedule = CaptureSchedule::SharedLightParallel;
    return true;
  }
  if (error_message != nullptr) {
    *error_message = "当前真实链路只支持 capture_schedule=shared_light_parallel: " + value;
  }
  return false;
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
        *error_message = "运行配置行缺少 '=': " + line;
      }
      return false;
    }
    const std::string key = trim(line.substr(0, eq));
    const std::string value = trim(line.substr(eq + 1));

    if (key == "hardware_mode") {
      if (!parse_hardware_mode(value, &config.hardware_mode, error_message)) return false;
    } else if (key == "controller_mode") {
      if (!parse_controller_mode(value, &config.controller_mode, error_message)) return false;
    } else if (key == "signal.backend" || key == "plc.backend") {
      if (!parse_hardware_backend(value, &config.signal.backend, error_message)) return false;
    } else if (key == "camera_backend" || key == "camera.backend") {
      if (!parse_hardware_backend(value, &config.camera_backend, error_message)) return false;
    } else if (key == "light.backend") {
      if (!parse_hardware_backend(value, &config.lights[0].backend, error_message)) return false;
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
    } else if (key == "signal.port" || key == "plc.port") {
      if (!parse_uint32_field("signal.port", value, false, &config.signal.port, error_message)) {
        return false;
      }
      if (config.signal.port > 65535) {
        if (error_message != nullptr) {
          *error_message = "signal.port 端口号必须在 1-65535: " + value;
        }
        return false;
      }
    } else if (key == "signal.delimiter" || key == "plc.delimiter") {
      config.signal.delimiter = value;
    } else if (key == "signal.terminator") {
      config.signal.terminator = value;
    } else if (key == "signal.ok_response") {
      config.signal.ok_response = value;
    } else if (key == "signal.result_host") {
      config.signal.result_host = value;
    } else if (key == "signal.result_port") {
      if (!parse_uint32_field("signal.result_port",
                              value,
                              false,
                              &config.signal.result_port,
                              error_message)) {
        return false;
      }
    } else if (key == "signal.result_prefix") {
      config.signal.result_prefix = value;
    } else if (key == "signal.result_delimiter") {
      config.signal.result_delimiter = value;
    } else if (key == "signal.ok_text") {
      config.signal.ok_text = value;
    } else if (key == "signal.ng_text") {
      config.signal.ng_text = value;
    } else if (key == "signal.recheck_text") {
      config.signal.recheck_text = value;
    } else if (key == "signal.error_text") {
      config.signal.error_text = value;
    } else if (key == "light.device_id") {
      config.lights[0].device_id = value;
    } else if (key == "light.host") {
      config.lights[0].host = value;
    } else if (key == "light.port") {
      if (!parse_uint32_field("light.port", value, false, &config.lights[0].port, error_message)) {
        return false;
      }
    } else if (key == "light.serial_port") {
      config.lights[0].serial_port = value;
    } else if (key == "light.baud_rate") {
      if (!parse_uint32_field("light.baud_rate",
                              value,
                              false,
                              &config.lights[0].baud_rate,
                              error_message)) {
        return false;
      }
    } else if (key == "light.trigger_input_line") {
      config.lights[0].trigger_input_line = value;
    } else if (key == "light.response_mode") {
      if (!parse_light_serial_response_mode_value(
              value, &config.lights[0].response_mode, error_message)) {
        return false;
      }
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
    } else if (key == "publish_timeout_ms") {
      if (!parse_int_field("publish_timeout_ms",
                           value,
                           false,
                           &config.publish_timeout_ms,
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
    } else if (key == "arm_settle_ms") {
      if (!parse_int_field("arm_settle_ms",
                           value,
                           true,
                           &config.arm_settle_ms,
                           error_message)) {
        return false;
      }
    } else if (key == "max_camera_failures_before_reset") {
      if (!parse_int_field("max_camera_failures_before_reset",
                           value,
                           false,
                           &config.max_camera_failures_before_reset,
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
      if (!parse_light_order(value, &config.light_order, error_message)) return false;
    } else if (key == "capture_mode") {
      if (!parse_capture_mode(value, &config.capture_mode, error_message)) return false;
    } else if (key == "capture_schedule") {
      if (!parse_capture_schedule(value, &config.capture_schedule, error_message)) return false;
    } else if (key == "reset_shared_memory") {
      if (!parse_bool_field(key, value, &config.reset_shared_memory, error_message)) return false;
    } else if (key == "simulate_light_fault" || key == "light.simulate_fault") {
      if (!parse_bool_field(key, value, &config.lights[0].simulate_fault, error_message)) {
        return false;
      }
    } else if (key == "simulate_signal_result_fault" ||
               key == "simulate_plc_output_fault" ||
               key == "signal.simulate_output_fault" ||
               key == "plc.simulate_output_fault") {
      if (!parse_bool_field(key, value, &config.signal.simulate_output_fault, error_message)) {
        return false;
      }
    } else if (key == "simulate_trigger_timeout" ||
               key == "signal.simulate_trigger_timeout" ||
               key == "plc.simulate_trigger_timeout") {
      if (!parse_bool_field(key, value, &config.signal.simulate_trigger_timeout, error_message)) {
        return false;
      }
    } else if (key == "simulate_missing_frame") {
      bool simulate_missing_frame = false;
      if (!parse_bool_field(key, value, &simulate_missing_frame, error_message)) return false;
      for (auto& camera : config.cameras) {
        camera.simulate_missing_frame = simulate_missing_frame;
      }
      for (auto& [camera_index, camera] : cameras) {
        (void)camera_index;
        camera.simulate_missing_frame = simulate_missing_frame;
      }
    } else if (key == "image_save.enabled") {
      if (!parse_bool_field(key, value, &config.image_save.enabled, error_message)) return false;
    } else if (key == "image_save.root_dir") {
      config.image_save.root_dir = value;
    } else if (key == "image_save.save_original") {
      if (!parse_bool_field(key, value, &config.image_save.save_original, error_message)) return false;
    } else if (key == "image_save.cleanup_enabled") {
      if (!parse_bool_field(key, value, &config.image_save.cleanup_enabled, error_message)) return false;
    } else if (key == "image_save.cleanup_min_free_ratio") {
      if (!parse_float_field(key,
                             value,
                             0.0F,
                             1.0F,
                             &config.image_save.cleanup_min_free_ratio,
                             error_message)) {
        return false;
      }
    } else if (key == "image_save.cleanup_trace_root") {
      if (!parse_bool_field(key, value, &config.image_save.cleanup_trace_root, error_message)) {
        return false;
      }
    } else if (key == "image_save.fail_on_save_error") {
      if (!parse_bool_field(key, value, &config.image_save.fail_on_save_error, error_message)) {
        return false;
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
          if (error_message != nullptr) {
            *error_message = "未知运行配置字段: " + key;
          }
          return false;
        }
      }
    }
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
  if (config.slot_count == 0) {
    if (error_message != nullptr) *error_message = "slot_count 必须大于 0";
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
    if (error_message != nullptr) *error_message = "所有 timeout_ms 配置都必须大于 0";
    return false;
  }
  if (config.max_camera_failures_before_reset <= 0) {
    if (error_message != nullptr) {
      *error_message = "max_camera_failures_before_reset 必须大于 0";
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
  if (config.capture_mode != CaptureMode::FixedCamera ||
      config.capture_schedule != CaptureSchedule::SharedLightParallel) {
    if (error_message != nullptr) {
      *error_message = "当前真实链路固定 capture_mode=fixed_camera 且 "
                       "capture_schedule=shared_light_parallel";
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
  // 验证 camera_index 从 0 开始连续编号
  for (std::uint32_t i = 0; i < config.cameras.size(); ++i) {
    if (!camera_indices[i]) {
      if (error_message != nullptr) {
        *error_message = "camera_index 必须从 0 开始连续编号，缺少 camera." + std::to_string(i);
      }
      return false;
    }
  }
  if (!validate_light_channels(config, error_message)) {
    return false;
  }
  const std::uint64_t expected_frame_count = config.cameras.size() * config.light_order.size();
  if (expected_frame_count == 0 || expected_frame_count > kMaxFramesPerJob) {
    if (error_message != nullptr) {
      *error_message = "相机数量 x 光源数量超过单任务最大帧数或为空";
    }
    return false;
  }
  if (estimated_payload_size(config) > config.frame_slot_size) {
    if (error_message != nullptr) {
      *error_message = "frame_slot_size 太小，无法容纳全部相机和光源的图像包";
    }
    return false;
  }

  const bool signal_is_simulated = is_simulated_backend(config.signal.backend);
  const bool signal_is_manual = is_manual_trigger_backend(config.signal.backend);
  const bool signal_is_external = is_external_signal_backend(config.signal.backend);
  const bool signal_is_tcp = config.signal.backend == HardwareBackend::TcpSignal;
  const bool camera_is_simulated = is_simulated_backend(config.camera_backend);
  const bool light_is_simulated = is_simulated_backend(config.lights[0].backend);

  if (config.hardware_mode == HardwareMode::Simulated) {
    if (!signal_is_simulated || !camera_is_simulated || !light_is_simulated) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=simulated 时 signal/camera/light backend 必须都是 simulated";
      }
      return false;
    }
    return true;
  }

  if (config.hardware_mode == HardwareMode::Lab) {
    if (!signal_is_manual && !signal_is_external && !signal_is_tcp) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=lab 只允许 manual_trigger/external_signal/tcp_signal";
      }
      return false;
    }
  } else {
    if (!signal_is_tcp && !signal_is_external) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=production 只允许 tcp_signal 或 external_signal";
      }
      return false;
    }
    if (signal_is_manual || signal_is_simulated || camera_is_simulated || light_is_simulated) {
      if (error_message != nullptr) {
        *error_message = "hardware_mode=production 禁止 simulated/manual backend";
      }
      return false;
    }
  }

  if (!reject_todo_if_set("signal.station_id", config.signal.station_id, error_message) ||
      !reject_todo_if_set("signal.default_seat_id",
                          config.signal.default_seat_id,
                          error_message) ||
      !reject_todo_if_set("signal.default_sku", config.signal.default_sku, error_message)) {
    return false;
  }
  if (!signal_is_manual &&
      (!require_non_empty("signal.station_id", config.signal.station_id, error_message) ||
       !require_non_empty("signal.default_sku", config.signal.default_sku, error_message))) {
    return false;
  }
  if (signal_is_tcp) {
    if (config.signal.port == 0 || config.signal.port > 65535) {
      if (error_message != nullptr) {
        *error_message = "signal.backend=tcp_signal 时 signal.port 必须配置 (1-65535)";
      }
      return false;
    }
    if (config.signal.station_id.empty()) {
      if (error_message != nullptr) {
        *error_message = "signal.backend=tcp_signal 时 signal.station_id 不能为空";
      }
      return false;
    }
  }
  for (const auto& camera : config.cameras) {
    const std::string prefix = "camera." + std::to_string(camera.camera_index);
    if (!reject_todo_if_set(prefix + ".camera_id", camera.camera_id, error_message) ||
        !reject_todo_if_set(prefix + ".pixel_format", camera.pixel_format, error_message)) {
      return false;
    }
    if (!camera_is_simulated &&
        (!require_non_empty(prefix + ".camera_id", camera.camera_id, error_message) ||
         !require_non_empty(prefix + ".serial_number", camera.serial_number, error_message) ||
         !require_non_empty(prefix + ".pixel_format", camera.pixel_format, error_message) ||
         !require_non_empty(prefix + ".trigger_line", camera.trigger_line, error_message) ||
         !require_non_empty(prefix + ".exposure_output_line",
                            camera.exposure_output_line,
                            error_message))) {
      return false;
    }
  }
  if (!light_is_simulated) {
    const auto& light = config.lights[0];
    if (light.backend != HardwareBackend::SerialAscii) {
      if (error_message != nullptr) {
        *error_message = "当前真实链路只支持 light.backend=serial_ascii";
      }
      return false;
    }
    if (!reject_todo_if_set("light.device_id", light.device_id, error_message) ||
        !reject_todo_if_set("light.serial_port", light.serial_port, error_message) ||
        !require_non_empty("light.serial_port", light.serial_port, error_message) ||
        !require_non_empty("light.trigger_input_line",
                           light.trigger_input_line,
                           error_message)) {
      return false;
    }
    if (light.baud_rate == 0) {
      if (error_message != nullptr) {
        *error_message = "light.baud_rate 必须大于 0";
      }
      return false;
    }
  }
  if (config.image_save.enabled && config.image_save.root_dir.empty()) {
    if (error_message != nullptr) {
      *error_message = "image_save.root_dir 不能为空";
    }
    return false;
  }
  if (config.controller_mode == ControllerMode::CaptureOnly &&
      (!config.image_save.enabled || !config.image_save.save_original)) {
    if (error_message != nullptr) {
      *error_message = "controller_mode=capture_only 必须启用 image_save.enabled/save_original";
    }
    return false;
  }
  return true;
}

}  // namespace seat_aoi
