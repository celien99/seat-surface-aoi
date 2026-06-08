#pragma once

#include "camera/camera_device.hpp"

namespace seat_aoi {

class CameraWorker {
public:
  bool initialize(const CameraConfig& config);
  void start();
  void stop();
  bool wait_frame(std::uint64_t trigger_id,
                  std::uint32_t light_index,
                  std::uint32_t light_seq_index,
                  CapturedFrame* out_frame,
                  int timeout_ms);
  CameraHealth get_health() const;

private:
  CameraDevice device_;
  bool running_ = false;
};

}  // namespace seat_aoi

