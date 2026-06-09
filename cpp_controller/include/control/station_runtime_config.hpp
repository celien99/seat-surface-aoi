#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "control/ilight_controller.hpp"
#include "ipc/shm_protocol.hpp"

namespace seat_aoi {

struct RuntimeCameraConfig {
  std::uint32_t camera_index = 0;
  std::string camera_id = "TOP_BACK";
  std::uint32_t width = 64;
  std::uint32_t height = 48;
  std::uint32_t channels = 1;
  bool simulate_missing_frame = false;
};

struct RuntimeLightConfig {
  bool simulate_fault = false;
  std::string message = "simulated";
};

struct RuntimePlcConfig {
  bool simulate_output_fault = false;
  bool simulate_trigger_timeout = false;
};

struct StationRuntimeConfig {
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
  TriggerSyncMode trigger_sync_mode = TriggerSyncMode::CameraExposureOutput;
  std::string trace_root = "trace";
  std::vector<RuntimeCameraConfig> cameras = {
      RuntimeCameraConfig{0, "TOP_BACK", 64, 48, 1, false},
      RuntimeCameraConfig{1, "TOP_CUSHION", 64, 48, 1, false},
  };
  RuntimeLightConfig light;
  RuntimePlcConfig plc;
};

bool load_station_runtime_config(const std::string& path,
                                 StationRuntimeConfig* out_config,
                                 std::string* error_message);

}  // namespace seat_aoi
