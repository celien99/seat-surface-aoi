#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "control/frame_assembler.hpp"
#include "control/iplc_client.hpp"
#include "control/station_runtime_config.hpp"
#include "control/trigger_scheduler.hpp"
#include "ipc/frame_ring_buffer.hpp"
#include "ipc/result_ring_buffer.hpp"

namespace seat_aoi {

struct StationConfig {
  bool reset_shared_memory = true;
  std::uint32_t slot_count = kDefaultSlotCount;
  std::uint32_t frame_slot_size = kDefaultFrameSlotSize;
  std::uint32_t result_slot_size = kDefaultResultSlotSize;
  int publish_timeout_ms = 1000;
  int detector_timeout_ms = 5000;
  int trigger_timeout_ms = 1000;
  int camera_timeout_ms = 200;
  int light_timeout_ms = 200;
  int max_jobs = 0;
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
  std::vector<RuntimeLightChannelConfig> light_channels = {
      RuntimeLightChannelConfig{1, 1, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{2, 2, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{3, 3, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{4, 4, 800, 800, 0, 1.0F, 60.0F},
  };
  TriggerSyncMode trigger_sync_mode = TriggerSyncMode::CameraExposureOutput;
  bool simulate_light_fault = false;
  bool simulate_plc_output_fault = false;
  bool simulate_trigger_timeout = false;
  bool simulate_missing_frame = false;
};

class StationController {
public:
  bool initialize(const StationConfig& config);
  bool wait_for_trigger(PlcTrigger* out_trigger, std::string* error_message);
  InspectionResultPayload inspect_one_seat(const PlcTrigger& trigger);
  void cleanup_shared_memory();

private:
  Recipe load_recipe(const std::string& sku) const;
  InspectionResultPayload make_recheck_result(const PlcTrigger& trigger,
                                               std::uint64_t sequence_id,
                                               ErrorCode error_code,
                                               const std::string& message) const;
  InspectionResultPayload make_and_send_recheck_result(const PlcTrigger& trigger,
                                                       std::uint64_t sequence_id,
                                                       ErrorCode error_code,
                                                       const std::string& message);
  bool validate_detector_result(const PlcTrigger& trigger,
                                std::uint64_t sequence_id,
                                const InspectionResultPayload& result,
                                std::string* error_message) const;
  void log_result(const InspectionResultPayload& result) const;

  StationConfig config_{};
  FrameRingBuffer frame_ring_;
  ResultRingBuffer result_ring_;
  FrameAssembler frame_assembler_;
  std::unique_ptr<IPlcClient> plc_client_;
  std::uint64_t next_sequence_id_ = 1;
};

}  // namespace seat_aoi
