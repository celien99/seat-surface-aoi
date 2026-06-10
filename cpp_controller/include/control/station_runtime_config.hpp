#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "control/hardware_backend.hpp"
#include "control/ilight_controller.hpp"
#include "ipc/shm_protocol.hpp"

namespace seat_aoi {

struct RuntimeCameraConfig {
  std::uint32_t camera_index = 0;
  std::string camera_id = "TOP_BACK";
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

struct RuntimeLightConfig {
  HardwareBackend backend = HardwareBackend::Simulated;
  std::string device_id;
  std::string host;
  std::uint32_t port = 0;
  std::string serial_port;
  std::uint32_t baud_rate = 0;
  std::string trigger_input_line;
  bool simulate_fault = false;
  std::string message = "simulated";
};

struct RuntimeLightChannelConfig {
  std::uint32_t light_index = 0;
  std::uint32_t physical_channel = 0;
  std::uint32_t exposure_us = 800;
  std::uint32_t strobe_width_us = 800;
  std::uint32_t trigger_delay_us = 0;
  float gain = 1.0F;
  float current_percent = 60.0F;
};

struct RuntimePlcConfig {
  HardwareBackend backend = HardwareBackend::Simulated;
  std::string host;
  std::uint32_t port = 0;
  std::string station_id;
  std::string trigger_source;
  std::string trigger_id_source;
  std::string seat_id_source;
  std::string sku_source;
  std::string ok_output;
  std::string ng_output;
  std::string recheck_output;
  std::string ack_input;
  std::uint32_t output_hold_ms = 200;
  bool simulate_output_fault = false;
  bool simulate_trigger_timeout = false;
};

struct StationRuntimeConfig {
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
  int max_jobs = 0;
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
  TriggerSyncMode trigger_sync_mode = TriggerSyncMode::CameraExposureOutput;
  std::string trace_root = "trace";
  std::vector<RuntimeCameraConfig> cameras = {
      RuntimeCameraConfig{0, "TOP_BACK", "", 64, 48, 1, "Mono8", "", "", 8, false},
      RuntimeCameraConfig{1, "TOP_CUSHION", "", 64, 48, 1, "Mono8", "", "", 8, false},
  };
  RuntimeLightConfig light;
  std::vector<RuntimeLightChannelConfig> light_channels = {
      RuntimeLightChannelConfig{1, 1, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{2, 2, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{3, 3, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{4, 4, 800, 800, 0, 1.0F, 60.0F},
  };
  RuntimePlcConfig plc;
};

bool load_station_runtime_config(const std::string& path,
                                 StationRuntimeConfig* out_config,
                                 std::string* error_message);
bool validate_station_runtime_config(const StationRuntimeConfig& config,
                                     std::string* error_message);

}  // namespace seat_aoi
