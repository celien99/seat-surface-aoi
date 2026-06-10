#pragma once

#include <cstdint>
#include <string>

#include "control/ilight_controller.hpp"
#include "ipc/frame_ring_buffer.hpp"

namespace seat_aoi {

struct CameraConfig {
  std::uint32_t camera_index = 0;
  std::string camera_id;
  std::string serial_number;
  std::uint32_t width = 64;
  std::uint32_t height = 48;
  std::uint32_t channels = 1;
  std::string pixel_format = "Mono8";
  std::string trigger_line;
  std::string exposure_output_line;
  std::uint32_t buffer_count = 8;
  bool simulate_missing_frame = false;
};

struct CameraHealth {
  bool ok = true;
  std::uint64_t dropped_frames = 0;
  std::string message = "simulated";
};

class ICamera {
public:
  virtual ~ICamera() = default;
  virtual bool initialize(const CameraConfig& config) = 0;
  virtual void start() = 0;
  virtual void stop() = 0;
  virtual bool arm(std::uint64_t trigger_id,
                   const LightChannelParam& light_param,
                   std::uint32_t light_seq_index,
                   int timeout_ms) = 0;
  virtual bool simulate_exposure_output(std::uint64_t trigger_id,
                                        const LightChannelParam& light_param,
                                        std::uint32_t light_seq_index,
                                        int timeout_ms) = 0;
  virtual bool wait_frame(std::uint64_t trigger_id,
                          const LightChannelParam& light_param,
                          std::uint32_t light_seq_index,
                          CapturedFrame* out_frame,
                          int timeout_ms) = 0;
  virtual CameraHealth get_health() const = 0;
};

}  // namespace seat_aoi
