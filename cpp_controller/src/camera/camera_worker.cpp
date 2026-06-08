#include "camera/camera_worker.hpp"

namespace seat_aoi {

bool CameraWorker::initialize(const CameraConfig& config) {
  return device_.initialize(config);
}

void CameraWorker::start() {
  running_ = true;
}

void CameraWorker::stop() {
  running_ = false;
}

bool CameraWorker::wait_frame(std::uint64_t trigger_id,
                              std::uint32_t light_index,
                              std::uint32_t light_seq_index,
                              CapturedFrame* out_frame,
                              int timeout_ms) {
  if (!running_) {
    return false;
  }
  return device_.capture(trigger_id, light_index, light_seq_index, out_frame, timeout_ms);
}

CameraHealth CameraWorker::get_health() const {
  return device_.get_health();
}

}  // namespace seat_aoi

