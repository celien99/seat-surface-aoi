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
};

enum class CaptureSchedule : std::uint32_t {
  SharedLightParallel = 1,
};

enum class ControllerMode : std::uint32_t {
  Online = 1,
  CaptureOnly = 2,
};

struct RuntimeCaptureSlotConfig {
  std::uint32_t view_index = 0;
  std::string view_id = "TOP_BACK";
  std::uint32_t camera_index = 0;
  std::string camera_id = "TOP_BACK";
  std::string calibration_id = "calib/simulated_v1";
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
  std::string replay_root;
  std::uint32_t replay_sample_index = 0;
  bool replay_random = false;
};

struct RuntimeLightConfig {
  HardwareBackend backend = HardwareBackend::Simulated;
  std::string device_id;
  std::string host;
  std::uint32_t port = 0;
  std::string serial_port;
  std::uint32_t baud_rate = 0;
  std::string trigger_input_line;
  LightSerialResponseMode response_mode = LightSerialResponseMode::Ack;
  bool simulate_fault = false;
  std::string message = "simulated";
};

struct RuntimeLightChannelConfig {
  std::uint32_t controller_index = 0;  // 所属控制器索引（0-based，来自 light.<M>.<N> 的 M）
  std::uint32_t light_index = 0;
  std::uint32_t physical_channel = 0;
  std::uint32_t exposure_us = 800;
  std::uint32_t strobe_width_us = 800;
  std::uint32_t trigger_delay_us = 10;
  float gain = 1.0F;
  float current_percent = 60.0F;
  LightAcquisitionMode acquisition_mode = LightAcquisitionMode::Strobe;
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
  // 两步协议模式配置 (protocol_mode="start_sn")
  std::string protocol_mode = "single";
  std::string start_command = "start";
  std::string sn_prefix = "sn";
  std::string start_ack = "start_ack\n";
  std::string sn_ack = "sn_ack\n";
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

struct ImageSaveConfig {
  bool enabled = false;
  std::string root_dir = "images";
  bool save_original = true;
  bool cleanup_enabled = true;
  float cleanup_min_free_ratio = 0.20F;
  bool cleanup_trace_root = true;
  bool fail_on_save_error = true;
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
  int trigger_timeout_ms = 0;  // 0 = 无限等待，有信号才执行
  int camera_timeout_ms = 200;
  int light_timeout_ms = 200;
  int arm_settle_ms = 50;
  int max_camera_failures_before_reset = 2;
  std::uint32_t warning_recheck_threshold = 3;
  std::uint32_t critical_recheck_threshold = 5;
  int max_jobs = 0;
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3};
  ControllerMode controller_mode = ControllerMode::Online;
  CaptureMode capture_mode = CaptureMode::FixedCamera;
  CaptureSchedule capture_schedule = CaptureSchedule::SharedLightParallel;
  std::string trace_root = "trace";
  std::vector<RuntimeCameraConfig> cameras = {
      RuntimeCameraConfig{0, "TOP_BACK", "", "calib/simulated_v1", 64, 48, 1, "Mono8", "", "", 8, false},
      RuntimeCameraConfig{1, "TOP_CUSHION", "", "calib/simulated_v1", 64, 48, 1, "Mono8", "", "", 8, false},
  };
  std::vector<RuntimeLightConfig> lights = {RuntimeLightConfig{}};
  std::vector<RuntimeLightChannelConfig> light_channels = {
      RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F, LightAcquisitionMode::Strobe},
      RuntimeLightChannelConfig{0, 2, 2, 800, 800, 10, 1.0F, 60.0F, LightAcquisitionMode::Strobe},
      RuntimeLightChannelConfig{0, 3, 3, 800, 800, 10, 1.0F, 55.0F, LightAcquisitionMode::Strobe},
  };
  RuntimeSignalConfig signal;
  ImageSaveConfig image_save;
};

const char* controller_mode_name(ControllerMode mode);
bool parse_controller_mode(const std::string& value,
                           ControllerMode* out_mode,
                           std::string* error_message);
const char* capture_mode_name(CaptureMode mode);
bool parse_capture_mode(const std::string& value,
                        CaptureMode* out_mode,
                        std::string* error_message);
const char* capture_schedule_name(CaptureSchedule schedule);
bool parse_capture_schedule(const std::string& value,
                            CaptureSchedule* out_schedule,
                            std::string* error_message);
bool load_station_runtime_config(const std::string& path,
                                 StationRuntimeConfig* out_config,
                                 std::string* error_message);
bool validate_station_runtime_config(const StationRuntimeConfig& config,
                                     std::string* error_message);

}  // namespace seat_aoi
