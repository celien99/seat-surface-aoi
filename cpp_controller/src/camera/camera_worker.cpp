#include "camera/camera_worker.hpp"

namespace seat_aoi {

bool SimCamera::initialize(const CameraConfig& config) {
  return device_.initialize(config);
}

void SimCamera::start() {
  running_ = true;
}

void SimCamera::stop() {
  running_ = false;
}

bool SimCamera::arm(std::uint64_t trigger_id,
                       const LightChannelParam& light_param,
                       std::uint32_t light_seq_index,
                       int timeout_ms) {
  if (!running_) {
    return false;
  }
  return device_.arm(trigger_id, light_param, light_seq_index, timeout_ms);
}

bool SimCamera::simulate_exposure_output(std::uint64_t trigger_id,
                                            const LightChannelParam& light_param,
                                            std::uint32_t light_seq_index,
                                            int timeout_ms) {
  if (!running_) {
    return false;
  }
  return device_.simulate_exposure_output(trigger_id, light_param, light_seq_index, timeout_ms);
}

bool SimCamera::wait_frame(std::uint64_t trigger_id,
                              const LightChannelParam& light_param,
                              std::uint32_t light_seq_index,
                              CapturedFrame* out_frame,
                              int timeout_ms) {
  if (!running_) {
    return false;
  }
  return device_.capture(trigger_id, light_param, light_seq_index, out_frame, timeout_ms);
}

CameraHealth SimCamera::get_health() const {
  return device_.get_health();
}

}  // namespace seat_aoi
