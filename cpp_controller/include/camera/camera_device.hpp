#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "control/light_controller.hpp"
#include "ipc/frame_ring_buffer.hpp"

namespace seat_aoi {

struct CameraConfig {
  std::uint32_t camera_index = 0;
  std::string camera_id;
  std::uint32_t width = 64;
  std::uint32_t height = 48;
  std::uint32_t channels = 1;
  bool simulate_missing_frame = false;
};

struct CameraHealth {
  bool ok = true;
  std::uint64_t dropped_frames = 0;
  std::string message = "simulated";
};

class CameraDevice {
public:
  bool initialize(const CameraConfig& config);
  bool capture(std::uint64_t trigger_id,
               const LightChannelParam& light_param,
               std::uint32_t light_seq_index,
               CapturedFrame* out_frame,
               int timeout_ms);
  CameraHealth get_health() const;

private:
  CameraConfig config_{};
  bool initialized_ = false;
};

}  // namespace seat_aoi
