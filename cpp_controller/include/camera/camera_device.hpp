#pragma once

#include <map>
#include <vector>

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
  void cancel_wait();
  CameraHealth get_health() const;

private:
  struct ReplayImage {
    std::uint64_t timestamp_us = 0;
    std::vector<std::uint8_t> bytes;
  };

  bool initialize_replay();
  bool capture_replay_frame(std::uint64_t trigger_id,
                            const LightChannelParam& light_param,
                            std::uint32_t light_seq_index,
                            CapturedFrame* out_frame);
  bool make_frame_meta(std::uint64_t trigger_id,
                       const LightChannelParam& light_param,
                       std::uint32_t light_seq_index,
                       CapturedFrame* out_frame) const;

  CameraConfig config_{};
  std::map<std::uint32_t, ReplayImage> replay_images_;
  bool replay_enabled_ = false;
  bool initialized_ = false;
  bool armed_ = false;
  std::uint64_t armed_trigger_id_ = 0;
  std::uint32_t armed_light_index_ = 0;
  std::uint32_t armed_light_seq_index_ = 0;
  std::string health_message_ = "simulated";
};

}  // namespace seat_aoi
