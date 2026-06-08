#include "camera/camera_device.hpp"

#include <chrono>
#include <thread>

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"

namespace seat_aoi {

bool CameraDevice::initialize(const CameraConfig& config) {
  config_ = config;
  initialized_ = true;
  return true;
}

bool CameraDevice::arm(std::uint64_t trigger_id,
                       const LightChannelParam& light_param,
                       std::uint32_t light_seq_index,
                       int timeout_ms) {
  if (!initialized_ || timeout_ms <= 0 || light_param.light_index == 0 ||
      light_param.exposure_us == 0) {
    return false;
  }
  armed_ = true;
  armed_trigger_id_ = trigger_id;
  armed_light_index_ = light_param.light_index;
  armed_light_seq_index_ = light_seq_index;
  return true;
}

bool CameraDevice::simulate_exposure_output(std::uint64_t trigger_id,
                                            const LightChannelParam& light_param,
                                            std::uint32_t light_seq_index,
                                            int timeout_ms) {
  if (!initialized_ || timeout_ms <= 0 || !armed_ || armed_trigger_id_ != trigger_id ||
      armed_light_index_ != light_param.light_index ||
      armed_light_seq_index_ != light_seq_index) {
    return false;
  }
  std::this_thread::sleep_for(std::chrono::milliseconds(1));
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
  if (armed_ && (armed_trigger_id_ != trigger_id ||
                 armed_light_index_ != light_param.light_index ||
                 armed_light_seq_index_ != light_seq_index)) {
    return false;
  }

  std::this_thread::sleep_for(std::chrono::milliseconds(2));
  const std::uint32_t stride = config_.width * config_.channels;
  const std::uint32_t image_size = stride * config_.height;
  out_frame->bytes.assign(image_size, 0);
  for (std::uint32_t y = 0; y < config_.height; ++y) {
    for (std::uint32_t x = 0; x < stride; ++x) {
      const std::uint32_t texture = ((x / 4U + y / 4U + light_param.light_index) % 2U) * 28U;
      const std::uint32_t gradient =
          (trigger_id + config_.camera_index * 17U + light_param.light_index * 11U + x +
           3U * y) %
          72U;
      out_frame->bytes[y * stride + x] = static_cast<std::uint8_t>(70U + texture + gradient);
    }
  }

  LightFrameMeta meta{};
  meta.camera_index = config_.camera_index;
  meta.light_index = light_param.light_index;
  meta.frame_index = static_cast<std::uint32_t>((trigger_id % 100000U) * 100U + light_seq_index);
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
  copy_cstr(meta.calibration_id, "calib/simulated_v1");
  out_frame->meta = meta;
  armed_ = false;
  return true;
}

CameraHealth CameraDevice::get_health() const {
  return CameraHealth{initialized_, 0, initialized_ ? "simulated" : "not initialized"};
}

}  // namespace seat_aoi
