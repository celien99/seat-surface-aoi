#pragma once

#include <string>

#include "camera/icamera.hpp"

namespace seat_aoi {

class HikrobotMvsCamera final : public ICamera {
public:
  HikrobotMvsCamera() = default;
  HikrobotMvsCamera(const HikrobotMvsCamera&) = delete;
  HikrobotMvsCamera& operator=(const HikrobotMvsCamera&) = delete;
  ~HikrobotMvsCamera() override;

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
  void cancel_wait() override;
  CameraHealth get_health() const override;

private:
  void close();
  void set_error(const std::string& message);

  CameraConfig config_{};
  void* handle_ = nullptr;
  bool sdk_initialized_ = false;
  bool initialized_ = false;
  bool grabbing_ = false;
  bool armed_ = false;
  bool healthy_ = false;
  std::uint64_t armed_trigger_id_ = 0;
  std::uint32_t armed_light_index_ = 0;
  std::uint32_t armed_light_seq_index_ = 0;
  std::uint64_t dropped_frames_ = 0;
  std::string health_message_ = "not initialized";
};

}  // namespace seat_aoi
