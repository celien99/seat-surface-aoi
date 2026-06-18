#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "control/hardware_backend.hpp"
#include "control/ilight_controller.hpp"
#include "ipc/shm_protocol.hpp"

namespace seat_aoi {

enum class CaptureMode : std::uint32_t {
  FixedCamera = 1,
  RobotFlyshot = 2,
};

struct RuntimeCaptureViewConfig {
  std::uint32_t pose_index = 0;
  std::string pose_id = "TOP_BACK";
  std::uint32_t camera_index = 0;
  std::string camera_id = "TOP_BACK";
  std::string calibration_id = "calib/simulated_v1";
  std::string shot_id_source;
  std::string robot_ready_input;
  std::string robot_fault_input;
  std::string photo_trigger_input;
  std::uint64_t simulated_shot_id = 0;
  float robot_tcp_xyz_mm[3] = {0.0F, 0.0F, 0.0F};
  float robot_rpy_deg[3] = {0.0F, 0.0F, 0.0F};
};

struct RuntimeCameraConfig {
  std::uint32_t camera_index = 0;
  std::string camera_id = "TOP_BACK";
  std::string serial_number;
  std::string calibration_id = "calib/simulated_v1";
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
  std::uint32_t controller_index = 0;  // 所属控制器索引（0-based，来自 light.<M>.<N> 的 M）
  std::uint32_t light_index = 0;
  std::uint32_t physical_channel = 0;
  std::uint32_t exposure_us = 800;
  std::uint32_t strobe_width_us = 800;
  std::uint32_t trigger_delay_us = 0;
  float gain = 1.0F;
  float current_percent = 60.0F;
};

struct RuntimeSignalConfig {
  HardwareBackend backend = HardwareBackend::Simulated;
  std::string station_id;
  std::string default_seat_id = "EXTERNAL_SEAT";
  std::string default_sku = "seat_a_black_leather";
  std::string trigger_queue_path;
  std::string result_queue_path;
  std::uint32_t port = 0;
  std::string delimiter;
  std::string terminator = "\n";
  std::string ok_response = "ok\n";
  // TCP 结果回传 (result_notify)
  std::string result_host;
  std::uint32_t result_port = 0;
  std::string result_prefix = "result";
  std::string result_delimiter = "|";
  std::string ok_text = "OK";
  std::string ng_text = "NG";
  std::string recheck_text = "RECHECK";
  std::string error_text = "ERROR";
  bool simulate_output_fault = false;
  bool simulate_trigger_timeout = false;
};

struct RuntimeRobotConfig {
  HardwareBackend backend = HardwareBackend::Simulated;
  std::string controller_id;
  std::string host;
  std::uint32_t port = 0;
  std::string ready_input;
  std::string fault_input;
  std::string start_output;
  bool simulate_fault = false;
};

struct ImageSaveConfig {
  bool enabled = false;
  std::string root_dir = "images";
  bool save_original = true;
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
  std::uint32_t warning_recheck_threshold = 3;
  std::uint32_t critical_recheck_threshold = 5;
  int max_jobs = 0;
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
  CaptureMode capture_mode = CaptureMode::FixedCamera;
  TriggerSyncMode trigger_sync_mode = TriggerSyncMode::CameraExposureOutput;
  std::string trace_root = "trace";
  std::vector<RuntimeCameraConfig> cameras = {
      RuntimeCameraConfig{0, "TOP_BACK", "", "calib/simulated_v1", 64, 48, 1, "Mono8", "", "", 8, false},
      RuntimeCameraConfig{1, "TOP_CUSHION", "", "calib/simulated_v1", 64, 48, 1, "Mono8", "", "", 8, false},
  };
  std::vector<RuntimeLightConfig> lights;
  std::vector<RuntimeLightChannelConfig> light_channels = {
      RuntimeLightChannelConfig{0, 1, 1, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{0, 2, 2, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{0, 3, 3, 800, 800, 0, 1.0F, 60.0F},
      RuntimeLightChannelConfig{0, 4, 4, 800, 800, 0, 1.0F, 60.0F},
  };
  std::vector<RuntimeCaptureViewConfig> capture_views;
  RuntimeSignalConfig signal;
  RuntimeRobotConfig robot;
  ImageSaveConfig image_save;
  bool json_output_enabled = false;
  std::string json_output_host;
  std::uint32_t json_output_port = 9002;
};

const char* capture_mode_name(CaptureMode mode);
bool parse_capture_mode(const std::string& value,
                        CaptureMode* out_mode,
                        std::string* error_message);
bool load_station_runtime_config(const std::string& path,
                                 StationRuntimeConfig* out_config,
                                 std::string* error_message);
bool validate_station_runtime_config(const StationRuntimeConfig& config,
                                     std::string* error_message);

}  // namespace seat_aoi
