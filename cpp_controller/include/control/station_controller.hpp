#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "control/frame_assembler.hpp"
#include "control/hardware_backend.hpp"
#include "control/isignal_client.hpp"
#include "control/production_event_log.hpp"
#include "control/station_health.hpp"
#include "control/station_runtime_config.hpp"
#include "control/trigger_scheduler.hpp"
#include "ipc/frame_ring_buffer.hpp"
#include "ipc/result_ring_buffer.hpp"

namespace seat_aoi {

struct StationConfig {
  HardwareMode hardware_mode = HardwareMode::Simulated;
  HardwareBackend camera_backend = HardwareBackend::Simulated;
  bool reset_shared_memory = true;
  std::uint32_t slot_count = kDefaultSlotCount;
  std::uint32_t frame_slot_size = kDefaultFrameSlotSize;
  std::uint32_t result_slot_size = kDefaultResultSlotSize;
  int publish_timeout_ms = 1000;
  int detector_timeout_ms = 5000;
  int trigger_timeout_ms = 1000;
  int camera_timeout_ms = 200;
  int light_timeout_ms = 200;
  std::uint32_t warning_recheck_threshold = 3;
  std::uint32_t critical_recheck_threshold = 5;
  int max_jobs = 0;
  std::string recipe_id = "seat_a_black_leather_v1";
  std::string trace_root = "trace";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
  CaptureMode capture_mode = CaptureMode::FixedCamera;
  std::vector<RuntimeCameraConfig> cameras = {
      RuntimeCameraConfig{0, "TOP_BACK", "", "calib/simulated_v1", 64, 48, 1, "Mono8", "", "", 8, false},
      RuntimeCameraConfig{1, "TOP_CUSHION", "", "calib/simulated_v1", 64, 48, 1, "Mono8", "", "", 8, false},
  };
  RuntimeLightConfig light;
  std::vector<RuntimeLightChannelConfig> light_channels = {
      RuntimeLightChannelConfig{0, 1, 1, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{0, 2, 2, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{0, 3, 3, 800, 800, 0, 1.0F, 55.0F},
      RuntimeLightChannelConfig{0, 4, 4, 800, 800, 0, 1.0F, 55.0F},
  };
  std::vector<RuntimeCaptureViewConfig> capture_views;
  RuntimeSignalConfig signal;
  RuntimeRobotConfig robot;
  bool simulate_light_fault = false;
  bool simulate_signal_result_fault = false;
  bool simulate_trigger_timeout = false;
  bool simulate_missing_frame = false;
  ImageSaveConfig image_save;
  bool json_output_enabled = false;
  std::string json_output_host;
  std::uint32_t json_output_port = 9002;
};

class StationController {
public:
  ~StationController();
  bool initialize(const StationConfig& config);
  bool wait_for_trigger(ExternalTrigger* out_trigger, std::string* error_message);
  InspectionResultPayload inspect_one_seat(const ExternalTrigger& trigger);
  StationHealthSnapshot health_snapshot() const;
  void cleanup_shared_memory();

private:
  Recipe load_recipe(const std::string& sku) const;
  InspectionResultPayload make_recheck_result(const ExternalTrigger& trigger,
                                               std::uint64_t sequence_id,
                                               ErrorCode error_code,
                                               const std::string& message) const;
  InspectionResultPayload make_and_send_recheck_result(const ExternalTrigger& trigger,
                                                       std::uint64_t sequence_id,
                                                       ErrorCode error_code,
                                                       const std::string& message);
  bool validate_detector_result(const ExternalTrigger& trigger,
                                std::uint64_t sequence_id,
                                const InspectionResultPayload& result,
                                std::string* error_message) const;
  void log_result(const InspectionResultPayload& result) const;
  void record_event(const std::string& name,
                    const ExternalTrigger& trigger,
                    std::uint64_t sequence_id,
                    InspectionDecision decision,
                    ErrorCode error_code,
                    const std::string& message);
  void record_system_event(const std::string& name,
                           ErrorCode error_code,
                           const std::string& message);
  void record_result_health(const InspectionResultPayload& result,
                            const std::string& message);

  StationConfig config_{};
  FrameRingBuffer frame_ring_;
  ResultRingBuffer result_ring_;
  FrameAssembler frame_assembler_;
  std::unique_ptr<ISignalClient> signal_client_;
  ProductionEventLog event_log_;
  StationHealthMonitor health_;
  std::uint64_t next_sequence_id_ = 1;
};

}  // namespace seat_aoi
