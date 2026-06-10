#include <array>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"
#include "control/hardware_factory.hpp"
#include "control/plc_client.hpp"
#include "control/station_health.hpp"
#include "control/station_controller.hpp"
#include "control/station_runtime_config.hpp"
#include "ipc/crc32.hpp"
#include "ipc/frame_ring_buffer.hpp"
#include "ipc/result_ring_buffer.hpp"

namespace {

std::uint32_t result_header_crc(const seat_aoi::ResultSlotHeader* slot) {
  std::array<std::uint8_t, sizeof(seat_aoi::ResultSlotHeader)> bytes{};
  std::memcpy(bytes.data(), slot, bytes.size());
  std::memset(bytes.data(), 0, sizeof(std::uint32_t));
  std::memset(bytes.data() + 20, 0, sizeof(std::uint32_t));
  return seat_aoi::crc32(bytes.data(), bytes.size());
}

std::uint8_t* slot_base(void* data, std::uint32_t slot_index, std::uint32_t slot_size) {
  return static_cast<std::uint8_t*>(data) + sizeof(seat_aoi::ShmHeader) +
         static_cast<std::size_t>(slot_index) * slot_size;
}

seat_aoi::SeatImageBundle make_bundle(std::uint64_t sequence_id) {
  seat_aoi::SeatImageBundle bundle;
  bundle.job_meta.sequence_id = sequence_id;
  bundle.job_meta.trigger_id = 1000 + sequence_id;
  seat_aoi::copy_cstr(bundle.job_meta.seat_id, "SIM");
  seat_aoi::copy_cstr(bundle.job_meta.sku, "seat_a_black_leather");
  seat_aoi::copy_cstr(bundle.job_meta.recipe_id, "seat_a_black_leather_v1");
  bundle.job_meta.camera_count = 1;
  bundle.job_meta.frame_count = 1;
  bundle.job_meta.created_at_us = seat_aoi::now_us();

  seat_aoi::CapturedFrame frame;
  frame.bytes = {80, 81, 82, 83};
  frame.meta.camera_index = 0;
  frame.meta.light_index = 1;
  frame.meta.frame_index = 1;
  frame.meta.light_seq_index = 0;
  frame.meta.width = 2;
  frame.meta.height = 2;
  frame.meta.channels = 1;
  frame.meta.stride_bytes = 2;
  frame.meta.pixel_format = static_cast<std::uint32_t>(seat_aoi::PixelFormat::Mono8);
  frame.meta.bit_depth = 8;
  frame.meta.color_order = static_cast<std::uint32_t>(seat_aoi::ColorOrder::Mono);
  frame.meta.dtype_code = static_cast<std::uint32_t>(seat_aoi::DTypeCode::UInt8);
  frame.meta.timestamp_us = seat_aoi::now_us();
  frame.meta.exposure_us = 800;
  frame.meta.gain = 1.0F;
  seat_aoi::copy_cstr(frame.meta.calibration_id, "calib/simulated_v1");
  bundle.frames.push_back(frame);
  return bundle;
}

bool test_frame_ring_skips_blocked_slot() {
  constexpr const char* kName = "/seat_aoi_test_frame_skip";
  constexpr std::uint32_t kSlotCount = 2;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::FrameRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "frame ring initialize failed\n";
    return false;
  }
  auto* first_slot = reinterpret_cast<seat_aoi::FrameSlotHeader*>(
      slot_base(ring.header(), 0, kSlotSize));
  first_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Reading),
                          std::memory_order_release);

  std::uint64_t sequence_id = 0;
  std::string error;
  const bool ok = ring.publish(make_bundle(7), 50, &sequence_id, &error);
  const auto* second_slot = reinterpret_cast<const seat_aoi::FrameSlotHeader*>(
      slot_base(ring.header(), 1, kSlotSize));
  const bool passed =
      ok && sequence_id == 7 &&
      second_slot->state.load(std::memory_order_acquire) ==
          static_cast<std::uint32_t>(seat_aoi::SlotState::Ready);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "frame ring did not skip blocked slot: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_returns_crc_error_immediately() {
  constexpr const char* kName = "/seat_aoi_test_result_crc";
  constexpr std::uint32_t kSlotCount = 1;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* base = slot_base(ring.header(), 0, kSlotSize);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  slot->sequence_id = 9;
  slot->payload_size = seat_aoi::result_slot_defects_offset();
  slot->payload_crc32 = 1234;
  slot->defect_count = 0;
  slot->reserved = 0;
  slot->result_meta.sequence_id = 9;
  slot->result_meta.trigger_id = 1009;
  seat_aoi::copy_cstr(slot->result_meta.seat_id, "SIM");
  slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK);
  slot->result_meta.defect_count = 0;
  slot->result_meta.quality_pass = 1;
  slot->result_meta.error_code = 0;
  slot->result_meta.elapsed_ms = 1.0F;
  slot->result_meta.reserved = 0;
  slot->header_crc32 = 0;
  slot->header_crc32 = result_header_crc(slot);
  slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                    std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(9, 200, &result, &error_code, &error);
  const bool passed = !ok && error_code == seat_aoi::ErrorCode::CrcMismatch &&
                      error == "result payload CRC mismatch" &&
                      slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring did not return CRC error immediately: " << error << "\n";
  }
  return passed;
}

bool test_plc_trigger_timeout_fails_closed() {
  seat_aoi::SimPlcClient plc;
  seat_aoi::PlcClientConfig config;
  config.simulate_trigger_timeout = true;
  if (!plc.initialize(config)) {
    std::cerr << "PLC initialize failed\n";
    return false;
  }
  seat_aoi::PlcTrigger trigger;
  std::string error;
  const bool ok = plc.wait_trigger(&trigger, 1, &error);
  const bool passed = !ok && error == "模拟 PLC 触发超时";
  if (!passed) {
    std::cerr << "PLC trigger timeout did not fail closed: " << error << "\n";
  }
  return passed;
}

bool test_station_fault_returns_recheck(const std::string& name,
                                        seat_aoi::StationConfig config,
                                        seat_aoi::ErrorCode expected_error) {
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{1, 1, 800, 800, 0, 1.0F, 60.0F},
  };

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << name << " station initialize failed\n";
    return false;
  }

  seat_aoi::PlcTrigger trigger;
  trigger.trigger_id = 7001;
  trigger.seat_id = "SIM_FAULT";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();

  const auto decision = static_cast<seat_aoi::InspectionDecision>(result.meta.decision);
  const auto error_code = static_cast<seat_aoi::ErrorCode>(result.meta.error_code);
  const bool passed = decision == seat_aoi::InspectionDecision::Recheck &&
                      error_code == expected_error &&
                      result.meta.quality_pass == 0 &&
                      result.meta.defect_count == 0;
  if (!passed) {
    std::cerr << name << " did not return RECHECK expected_error="
              << static_cast<std::uint32_t>(expected_error)
              << " actual_decision=" << result.meta.decision
              << " actual_error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_light_fault_returns_recheck() {
  seat_aoi::StationConfig config;
  config.simulate_light_fault = true;
  return test_station_fault_returns_recheck("light fault",
                                            config,
                                            seat_aoi::ErrorCode::LightFault);
}

bool test_missing_frame_returns_recheck() {
  seat_aoi::StationConfig config;
  config.simulate_missing_frame = true;
  return test_station_fault_returns_recheck("missing frame",
                                            config,
                                            seat_aoi::ErrorCode::MissingFrame);
}

bool test_frame_slot_unavailable_returns_recheck() {
  seat_aoi::StationConfig config;
  config.slot_count = 0;
  config.frame_slot_size = 4096;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 1;
  config.detector_timeout_ms = 1;
  config.trigger_timeout_ms = 1;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.light_order = {1};

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "slot unavailable station initialize failed\n";
    return false;
  }

  seat_aoi::PlcTrigger trigger;
  trigger.trigger_id = 7002;
  trigger.seat_id = "SIM_SLOT_FULL";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();

  const auto decision = static_cast<seat_aoi::InspectionDecision>(result.meta.decision);
  const auto error_code = static_cast<seat_aoi::ErrorCode>(result.meta.error_code);
  const bool passed = decision == seat_aoi::InspectionDecision::Recheck &&
                      error_code == seat_aoi::ErrorCode::SlotUnavailable &&
                      result.meta.quality_pass == 0 &&
                      result.meta.defect_count == 0;
  if (!passed) {
    std::cerr << "slot unavailable did not return RECHECK actual_decision="
              << result.meta.decision << " actual_error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_runtime_light_channel_config_parses() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  if (!seat_aoi::load_station_runtime_config(
          "cpp_controller/config/station_runtime.example.conf", &config, &error)) {
    error.clear();
    if (!seat_aoi::load_station_runtime_config(
            "config/station_runtime.example.conf", &config, &error)) {
      std::cerr << "runtime config parse failed: " << error << "\n";
      return false;
    }
  }
  const bool passed = config.light_channels.size() >= 4 &&
                      config.light_channels[0].light_index == 1 &&
                      config.light_channels[0].physical_channel == 1 &&
                      config.light_channels[0].exposure_us == 800 &&
                      config.light_channels[0].strobe_width_us == 700 &&
                      config.light_channels[0].trigger_delay_us == 10 &&
                      config.light_channels[0].gain == 1.0F &&
                      config.light_channels[0].current_percent == 60.0F &&
                      config.light_channels[2].light_index == 3 &&
                      config.light_channels[2].physical_channel == 3 &&
                      config.light_channels[2].strobe_width_us == 650 &&
                      config.light_channels[2].current_percent == 55.0F;
  if (!passed) {
    std::cerr << "runtime light channel config did not parse expected values\n";
  }
  return passed;
}

bool test_production_template_rejects_todo_placeholders() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  bool ok = seat_aoi::load_station_runtime_config(
      "cpp_controller/config/station_runtime.production.example.conf", &config, &error);
  if (ok) {
    std::cerr << "production template with TODO placeholders unexpectedly passed\n";
    return false;
  }
  if (error.empty()) {
    error.clear();
    ok = seat_aoi::load_station_runtime_config(
        "config/station_runtime.production.example.conf", &config, &error);
    if (ok) {
      std::cerr << "production template with TODO placeholders unexpectedly passed\n";
      return false;
    }
  }
  const bool passed = error.find("TODO") != std::string::npos ||
                      error.find("占位") != std::string::npos;
  if (!passed) {
    std::cerr << "production template rejection did not mention placeholder: " << error << "\n";
  }
  return passed;
}

seat_aoi::StationRuntimeConfig make_filled_production_runtime_config() {
  seat_aoi::StationRuntimeConfig config;
  config.hardware_mode = seat_aoi::HardwareMode::Production;
  config.plc.backend = seat_aoi::HardwareBackend::ModbusTcp;
  config.camera_backend = seat_aoi::HardwareBackend::HikrobotMvs;
  config.light.backend = seat_aoi::HardwareBackend::SerialAscii;
  config.frame_slot_size = 64 * 1024 * 1024;
  config.plc.host = "192.168.1.10";
  config.plc.port = 502;
  config.plc.station_id = "LINE1_AOI_01";
  config.plc.trigger_source = "DI0";
  config.plc.trigger_id_source = "HR100";
  config.plc.seat_id_source = "HR120";
  config.plc.sku_source = "HR160";
  config.plc.ok_output = "DO0";
  config.plc.ng_output = "DO1";
  config.plc.recheck_output = "DO2";
  config.plc.ack_input = "DI1";
  config.light.device_id = "STROBE_01";
  config.light.serial_port = "/dev/ttyUSB0";
  config.light.baud_rate = 115200;
  config.light.trigger_input_line = "TRIG_IN1";
  for (auto& camera : config.cameras) {
    camera.serial_number = "CAM_SN_" + std::to_string(camera.camera_index);
    camera.trigger_line = "Line0";
    camera.exposure_output_line = "Line1";
  }
  return config;
}

bool test_filled_production_config_validates() {
  auto config = make_filled_production_runtime_config();
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  if (!ok) {
    std::cerr << "filled production config did not validate: " << error << "\n";
  }
  return ok;
}

bool test_parallel_acquisition_strategy_rejected() {
  auto config = make_filled_production_runtime_config();
  config.acquisition_strategy = static_cast<seat_aoi::AcquisitionStrategy>(999U);
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("serial_tdm") != std::string::npos;
  if (!passed) {
    std::cerr << "parallel acquisition strategy was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_strobe_width_larger_than_exposure_rejected() {
  auto config = make_filled_production_runtime_config();
  config.light_channels[0].strobe_width_us = config.light_channels[0].exposure_us + 1U;
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("脉宽") != std::string::npos;
  if (!passed) {
    std::cerr << "invalid strobe width was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_invalid_health_threshold_rejected() {
  auto config = make_filled_production_runtime_config();
  config.warning_recheck_threshold = 3;
  config.critical_recheck_threshold = 3;
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("critical_recheck_threshold") != std::string::npos;
  if (!passed) {
    std::cerr << "invalid health threshold was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_health_monitor_escalates_after_consecutive_rechecks() {
  seat_aoi::StationHealthMonitor health;
  health.configure(2, 3);
  health.transition_to(seat_aoi::StationState::Ready, "ready");
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::DetectorTimeout,
                       "timeout 1");
  const auto warning = health.snapshot();
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::DetectorTimeout,
                       "timeout 2");
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::DetectorTimeout,
                       "timeout 3");
  const auto critical = health.snapshot();
  const bool passed = warning.alarm_level == seat_aoi::AlarmLevel::Warning &&
                      critical.alarm_level == seat_aoi::AlarmLevel::Critical &&
                      critical.state == seat_aoi::StationState::Fault &&
                      critical.consecutive_recheck_count == 3;
  if (!passed) {
    std::cerr << "health monitor did not escalate after consecutive rechecks\n";
  }
  return passed;
}

bool test_station_writes_detector_timeout_event_log() {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{1, 1, 800, 800, 0, 1.0F, 60.0F},
  };
  config.trace_root = "/tmp/seat_aoi_cpp_event_log_test_" +
                      std::to_string(seat_aoi::now_us());

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "event log station initialize failed\n";
    return false;
  }

  seat_aoi::PlcTrigger trigger;
  trigger.trigger_id = 7010;
  trigger.seat_id = "SIM_TIMEOUT";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();

  std::ifstream input(config.trace_root + "/cpp_controller_events.jsonl");
  std::string line;
  bool saw_timeout = false;
  while (std::getline(input, line)) {
    if (line.find("\"event\":\"inspection_recheck\"") != std::string::npos &&
        line.find("\"error\":\"DetectorTimeout\"") != std::string::npos &&
        line.find("\"trigger_id\":7010") != std::string::npos) {
      saw_timeout = true;
      break;
    }
  }

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::DetectorTimeout &&
      saw_timeout;
  if (!passed) {
    std::cerr << "detector timeout event log was not written\n";
  }
  return passed;
}

bool test_unsupported_production_backend_fails_fast() {
  auto plc = seat_aoi::create_plc_client(seat_aoi::HardwareBackend::ModbusTcp);
  seat_aoi::PlcClientConfig config;
  const bool ok = plc->initialize(config);
  const auto health = plc->get_health();
  const bool passed = !ok && !health.ok &&
                      health.message.find("尚未链接真实硬件驱动") != std::string::npos;
  if (!passed) {
    std::cerr << "unsupported production backend did not fail fast: "
              << health.message << "\n";
    return false;
  }
  return true;
}

}  // namespace

int main() {
  if (!test_frame_ring_skips_blocked_slot()) {
    return 1;
  }
  if (!test_result_ring_returns_crc_error_immediately()) {
    return 1;
  }
  if (!test_plc_trigger_timeout_fails_closed()) {
    return 1;
  }
  if (!test_light_fault_returns_recheck()) {
    return 1;
  }
  if (!test_missing_frame_returns_recheck()) {
    return 1;
  }
  if (!test_frame_slot_unavailable_returns_recheck()) {
    return 1;
  }
  if (!test_runtime_light_channel_config_parses()) {
    return 1;
  }
  if (!test_production_template_rejects_todo_placeholders()) {
    return 1;
  }
  if (!test_filled_production_config_validates()) {
    return 1;
  }
  if (!test_parallel_acquisition_strategy_rejected()) {
    return 1;
  }
  if (!test_strobe_width_larger_than_exposure_rejected()) {
    return 1;
  }
  if (!test_invalid_health_threshold_rejected()) {
    return 1;
  }
  if (!test_health_monitor_escalates_after_consecutive_rechecks()) {
    return 1;
  }
  if (!test_station_writes_detector_timeout_event_log()) {
    return 1;
  }
  if (!test_unsupported_production_backend_fails_fast()) {
    return 1;
  }
  std::cout << "ipc safety checks passed\n";
  return 0;
}
