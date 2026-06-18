#pragma once

#include "camera/icamera.hpp"

namespace seat_aoi {

// CameraConfig, CameraHealth moved to icamera.hpp

class CameraDevice {
public:
  bool initialize(const CameraConfig& config);
  bool arm(std::uint64_t trigger_id,
           const LightChannelParam& light_param,
           std::uint32_t light_seq_index,
           int timeout_ms);
  bool capture(std::uint64_t trigger_id,
               const LightChannelParam& light_param,
               std::uint32_t light_seq_index,
               CapturedFrame* out_frame,
               int timeout_ms);
  CameraHealth get_health() const;

private:
  CameraConfig config_{};
  bool initialized_ = false;
  bool armed_ = false;
  std::uint64_t armed_trigger_id_ = 0;
  std::uint32_t armed_light_index_ = 0;
  std::uint32_t armed_light_seq_index_ = 0;
};

}  // namespace seat_aoi
