#pragma once

#include "camera/camera_device.hpp"
#include "camera/icamera.hpp"

namespace seat_aoi {

class SimCamera : public ICamera {
public:
  bool initialize(const CameraConfig& config) override;
  void start() override;
  void stop() override;
  bool arm(std::uint64_t trigger_id,
           const LightChannelParam& light_param,
           std::uint32_t light_seq_index,
           int timeout_ms) override;
  bool wait_frame(std::uint64_t trigger_id,
                  const LightChannelParam& light_param,
                  std::uint32_t light_seq_index,
                  CapturedFrame* out_frame,
                  int timeout_ms) override;
  CameraHealth get_health() const override;

private:
  CameraDevice device_;
  bool running_ = false;
};


}  // namespace seat_aoi
