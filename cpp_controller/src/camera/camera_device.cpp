#include "camera/camera_device.hpp"

#include <chrono>
#include <algorithm>
#include <filesystem>
#include <map>
#include <mutex>
#include <random>
#include <sstream>
#include <thread>

#include "camera/replay_capture.hpp"
#include "common/png_reader.hpp"
#include "common/string_utils.hpp"
#include "common/time_utils.hpp"

namespace seat_aoi {

namespace {

std::mutex g_replay_selection_mutex;
std::map<std::string, std::uint32_t> g_random_replay_sample_by_root;

std::string normalize_replay_root(const std::string& root) {
  std::error_code ec;
  const auto absolute = std::filesystem::absolute(root, ec);
  if (ec) {
    return root;
  }
  return absolute.lexically_normal().string();
}

std::uint32_t select_replay_sample_index(const std::string& replay_root,
                                         const std::vector<std::uint32_t>& complete_indices,
                                         bool replay_random,
                                         std::uint32_t configured_index) {
  if (!replay_random) {
    return configured_index > 0 ? configured_index : 1;
  }

  const auto key = normalize_replay_root(replay_root);
  std::lock_guard<std::mutex> lock(g_replay_selection_mutex);
  const auto iter = g_random_replay_sample_by_root.find(key);
  if (iter != g_random_replay_sample_by_root.end()) {
    return iter->second;
  }
  std::random_device device;
  std::mt19937 generator(device());
  std::uniform_int_distribution<std::size_t> distribution(0, complete_indices.size() - 1U);
  const auto selected = complete_indices[distribution(generator)];
  g_random_replay_sample_by_root[key] = selected;
  return selected;
}

}  // namespace

bool CameraDevice::initialize(const CameraConfig& config) {
  config_ = config;
  replay_enabled_ = !config_.replay_root.empty();
  replay_images_.clear();
  health_message_ = "simulated";
  if (replay_enabled_ && !initialize_replay()) {
    initialized_ = false;
    return false;
  }
  initialized_ = true;
  return true;
}

bool CameraDevice::arm(std::uint64_t trigger_id,
                       const LightChannelParam& light_param,
                       std::uint32_t light_seq_index,
                       int timeout_ms) {
  if (!initialized_ || timeout_ms <= 0) { return false; }
  armed_ = true;
  armed_trigger_id_ = trigger_id;
  armed_light_index_ = light_param.light_index;
  armed_light_seq_index_ = light_seq_index;
  return true;
}

bool CameraDevice::capture(std::uint64_t trigger_id,
                           const LightChannelParam& light_param,
                           std::uint32_t light_seq_index,
                           CapturedFrame* out_frame,
                           int timeout_ms) {
  if (!initialized_ || out_frame == nullptr || timeout_ms <= 0) {
    return false;
  }
  if (config_.simulate_missing_frame) {
    return false;
  }
  // 流水线采集模式下不再校验 armed_ 状态对齐，调用方保证 arm 已先行完成。
  if (replay_enabled_) {
    return capture_replay_frame(trigger_id, light_param, light_seq_index, out_frame);
  }

  std::this_thread::sleep_for(std::chrono::milliseconds(2));
  const std::uint32_t stride = config_.width * config_.channels;
  const std::uint32_t image_size = stride * config_.height;
  out_frame->bytes.assign(image_size, 0);
  for (std::uint32_t y = 0; y < config_.height; ++y) {
    for (std::uint32_t x = 0; x < stride; ++x) {
      const std::uint32_t texture = ((x / 4U + y / 4U + 1U) % 2U) * 28U;
      const std::uint32_t gradient =
          (trigger_id + config_.camera_index * 17U +
      light_param.light_index * 11U + x + 3U * y) % 72U;
      out_frame->bytes[y * stride + x] = static_cast<std::uint8_t>(70U + texture + gradient);
    }
  }

  if (!make_frame_meta(trigger_id, light_param, light_seq_index, out_frame)) {
    return false;
  }
  out_frame->meta.timestamp_us = now_us();
  armed_ = false;
  return true;
}

void CameraDevice::cancel_wait() {
  armed_ = false;
}

CameraHealth CameraDevice::get_health() const {
  return CameraHealth{initialized_, 0, initialized_ ? health_message_ : health_message_};
}

bool CameraDevice::initialize_replay() {
  if (config_.channels != 1 || config_.pixel_format != "Mono8") {
    health_message_ = "images_capture replay only supports Mono8 single-channel cameras";
    return false;
  }

  std::string scan_error;
  const auto groups = scan_replay_capture_groups(config_.replay_root,
                                                 config_.camera_id,
                                                 config_.replay_required_lights,
                                                 &scan_error);
  if (groups.empty()) {
    health_message_ = scan_error.empty() ? "no replay capture groups found" : scan_error;
    return false;
  }
  const auto complete_indices = complete_replay_sample_indices(groups, config_.replay_required_lights);
  if (complete_indices.empty()) {
    health_message_ = "no complete replay capture groups found for camera_id=" + config_.camera_id;
    return false;
  }
  const auto selected_index = select_replay_sample_index(config_.replay_root,
                                                        complete_indices,
                                                        config_.replay_random,
                                                        config_.replay_sample_index);
  const auto group_iter = std::find_if(groups.begin(), groups.end(), [selected_index](const auto& group) {
    return group.sample_index == selected_index;
  });
  if (group_iter == groups.end()) {
    std::ostringstream message;
    message << "replay_sample_index=" << selected_index << " out of range 1.."
            << groups.back().sample_index << " camera_id=" << config_.camera_id;
    health_message_ = message.str();
    return false;
  }
  if (!is_complete_replay_group(*group_iter, config_.replay_required_lights)) {
    std::ostringstream message;
    message << "replay_sample_index=" << selected_index
            << " is incomplete camera_id=" << config_.camera_id;
    health_message_ = message.str();
    return false;
  }

  replay_images_.clear();
  for (const auto light_index : config_.replay_required_lights) {
    const auto file_iter = group_iter->files_by_light.find(light_index);
    if (file_iter == group_iter->files_by_light.end()) {
      std::ostringstream message;
      message << "replay missing selected light_index=" << light_index
              << " camera_id=" << config_.camera_id;
      health_message_ = message.str();
      return false;
    }
    PngImage image;
    std::string error;
    const auto& path = file_iter->second.path;
    if (!read_png_image(path.string(), &image, &error)) {
      health_message_ = "failed to decode replay PNG: " + error;
      return false;
    }
    if (image.channels != 1 || image.width != config_.width ||
        image.height != config_.height) {
      std::ostringstream message;
      message << "replay PNG shape mismatch path=" << path.string()
              << " actual=" << image.width << "x" << image.height
              << "x" << image.channels << " expected=" << config_.width
              << "x" << config_.height << "x1";
      health_message_ = message.str();
      return false;
    }
    replay_images_[light_index] = ReplayImage{file_iter->second.timestamp_us, std::move(image.pixels)};
  }

  std::ostringstream message;
  message << "replay images_capture sample_index=" << selected_index
          << " camera_id=" << config_.camera_id
          << " lights=" << replay_images_.size();
  health_message_ = message.str();
  return true;
}

bool CameraDevice::capture_replay_frame(std::uint64_t trigger_id,
                                        const LightChannelParam& light_param,
                                        std::uint32_t light_seq_index,
                                        CapturedFrame* out_frame) {
  const auto iter = replay_images_.find(light_param.light_index);
  if (iter == replay_images_.end()) {
    std::ostringstream message;
    message << "replay missing light_index=" << light_param.light_index
            << " camera_id=" << config_.camera_id;
    health_message_ = message.str();
    return false;
  }
  out_frame->bytes = iter->second.bytes;
  if (!make_frame_meta(trigger_id, light_param, light_seq_index, out_frame)) {
    return false;
  }
  out_frame->meta.timestamp_us = now_us();
  armed_ = false;
  return true;
}

bool CameraDevice::make_frame_meta(std::uint64_t trigger_id,
                                   const LightChannelParam& light_param,
                                   std::uint32_t light_seq_index,
                                   CapturedFrame* out_frame) const {
  if (out_frame == nullptr) {
    return false;
  }
  const std::uint32_t stride = config_.width * config_.channels;
  LightFrameMeta meta{};
  meta.camera_index = config_.camera_index;
  meta.light_index = light_param.light_index;
  meta.frame_index = static_cast<std::uint32_t>((trigger_id % 100000U) * 100U +
                                                light_seq_index);
  meta.light_seq_index = light_seq_index;
  meta.width = config_.width;
  meta.height = config_.height;
  meta.channels = config_.channels;
  meta.stride_bytes = stride;
  meta.pixel_format = static_cast<std::uint32_t>(PixelFormat::Mono8);
  meta.bit_depth = 8;
  meta.color_order = static_cast<std::uint32_t>(ColorOrder::Mono);
  meta.dtype_code = static_cast<std::uint32_t>(DTypeCode::UInt8);
  meta.timestamp_us = now_us();
  meta.exposure_us = light_param.exposure_us;
  meta.gain = light_param.gain;
  copy_cstr(meta.camera_id, config_.camera_id);
  copy_cstr(meta.view_id, config_.camera_id);
  copy_cstr(meta.calibration_id, config_.calibration_id);
  out_frame->meta = meta;
  return true;
}

}  // namespace seat_aoi
