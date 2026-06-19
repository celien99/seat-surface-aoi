#include <array>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"
#include "control/image_writer.hpp"
#include "control/hardware_factory.hpp"
#include "control/signal_client.hpp"
#include "control/station_health.hpp"
#include "control/station_controller.hpp"
#include "control/station_runtime_config.hpp"
#include "ipc/crc32.hpp"
#include "ipc/frame_ring_buffer.hpp"
#include "ipc/result_ring_buffer.hpp"
#include "ipc/shared_memory.hpp"

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
  bundle.job_meta.view_count = 1;
  bundle.job_meta.frame_count = 1;
  bundle.job_meta.capture_mode = static_cast<std::uint32_t>(seat_aoi::CaptureMode::FixedCamera);
  bundle.job_meta.created_at_us = seat_aoi::now_us();

  seat_aoi::CapturedFrame frame;
  frame.bytes = {80, 81, 82, 83};
  frame.meta.camera_index = 0;
  frame.meta.pose_index = 0;
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
  frame.meta.shot_id = 1000 + sequence_id;
  frame.meta.robot_timestamp_us = frame.meta.timestamp_us;
  frame.meta.exposure_us = 800;
  frame.meta.gain = 1.0F;
  seat_aoi::copy_cstr(frame.meta.camera_id, "TOP_BACK");
  seat_aoi::copy_cstr(frame.meta.pose_id, "TOP_BACK");
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

bool test_result_ring_rejects_payload_size_mismatch() {
  constexpr const char* kName = "/seat_aoi_res_size";
  constexpr std::uint32_t kSlotCount = 1;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* base = slot_base(ring.header(), 0, kSlotSize);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  slot->sequence_id = 19;
  slot->payload_size = seat_aoi::result_slot_defects_offset();
  slot->payload_crc32 = seat_aoi::crc32(base + seat_aoi::result_slot_defects_offset(), 0);
  slot->defect_count = 1;
  slot->reserved = 0;
  slot->result_meta.sequence_id = 19;
  slot->result_meta.trigger_id = 1019;
  seat_aoi::copy_cstr(slot->result_meta.seat_id, "SIM");
  slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::NG);
  slot->result_meta.defect_count = 1;
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
  const bool ok = ring.wait_for_result(19, 200, &result, &error_code, &error);
  const bool passed = !ok && error_code == seat_aoi::ErrorCode::InvalidPayload &&
                      error == "invalid result payload size or defect count" &&
                      slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring accepted mismatched payload size: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_reclaims_stale_and_bad_slots() {
  constexpr const char* kName = "/seat_aoi_res_reclaim";
  constexpr std::uint32_t kSlotCount = 3;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* stale_base = slot_base(ring.header(), 0, kSlotSize);
  auto* stale_slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(stale_base);
  stale_slot->sequence_id = 3;
  stale_slot->payload_size = seat_aoi::result_slot_defects_offset();
  stale_slot->payload_crc32 = seat_aoi::crc32(stale_base + seat_aoi::result_slot_defects_offset(), 0);
  stale_slot->defect_count = 0;
  stale_slot->result_meta.sequence_id = 3;
  stale_slot->result_meta.trigger_id = 1003;
  seat_aoi::copy_cstr(stale_slot->result_meta.seat_id, "SIM");
  stale_slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK);
  stale_slot->result_meta.quality_pass = 1;
  stale_slot->header_crc32 = 0;
  stale_slot->header_crc32 = result_header_crc(stale_slot);
  stale_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                          std::memory_order_release);

  auto* bad_base = slot_base(ring.header(), 1, kSlotSize);
  auto* bad_slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(bad_base);
  bad_slot->sequence_id = 4;
  bad_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Corrupted),
                        std::memory_order_release);

  auto* current_base = slot_base(ring.header(), 2, kSlotSize);
  auto* current_slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(current_base);
  current_slot->sequence_id = 5;
  current_slot->payload_size = seat_aoi::result_slot_defects_offset();
  current_slot->payload_crc32 = seat_aoi::crc32(current_base + seat_aoi::result_slot_defects_offset(), 0);
  current_slot->defect_count = 0;
  current_slot->result_meta.sequence_id = 5;
  current_slot->result_meta.trigger_id = 1005;
  seat_aoi::copy_cstr(current_slot->result_meta.seat_id, "SIM");
  current_slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK);
  current_slot->result_meta.defect_count = 0;
  current_slot->result_meta.quality_pass = 1;
  current_slot->result_meta.error_code = 0;
  current_slot->result_meta.elapsed_ms = 1.0F;
  current_slot->result_meta.reserved = 0;
  current_slot->header_crc32 = 0;
  current_slot->header_crc32 = result_header_crc(current_slot);
  current_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                            std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(5, 200, &result, &error_code, &error);
  const bool passed = ok && result.meta.sequence_id == 5 &&
                      error_code == seat_aoi::ErrorCode::None &&
                      stale_slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty) &&
                      bad_slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty) &&
                      current_slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring let stale/bad slots block current result: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_returns_current_bad_slot_error() {
  constexpr const char* kName = "/seat_aoi_res_badcur";
  constexpr std::uint32_t kSlotCount = 1;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* base = slot_base(ring.header(), 0, kSlotSize);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  slot->sequence_id = 5;
  slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Corrupted),
                    std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(5, 200, &result, &error_code, &error);
  const bool passed = !ok && error_code == seat_aoi::ErrorCode::CrcMismatch &&
                      error == "result slot marked corrupted" &&
                      slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty) &&
                      ring.header()->read_index == 1;
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring did not return current bad slot error: " << error << "\n";
  }
  return passed;
}

bool test_ring_layout_mismatch_fails_without_reset() {
  constexpr const char* kName = "/seat_aoi_layout";
  seat_aoi::FrameRingBuffer first;
  if (!first.initialize(kName, 1, 4096, true)) {
    std::cerr << "initial frame ring initialize failed\n";
    return false;
  }

  seat_aoi::FrameRingBuffer second;
  const bool ok = second.initialize(kName, 1, 8192, false);
  second.unlink_name();
  second.close();
  first.unlink_name();
  first.close();
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "frame ring layout mismatch did not fail without reset\n";
  }
  return passed;
}

bool test_signal_trigger_timeout_fails_closed() {
  seat_aoi::SimSignalClient signal;
  seat_aoi::SignalClientConfig config;
  config.simulate_trigger_timeout = true;
  if (!signal.initialize(config)) {
    std::cerr << "signal client initialize failed\n";
    return false;
  }
  seat_aoi::ExternalTrigger trigger;
  std::string error;
  const bool ok = signal.wait_trigger(&trigger, 1, &error);
  const bool passed = !ok && error == "模拟外部信号触发超时";
  if (!passed) {
    std::cerr << "signal trigger timeout did not fail closed: " << error << "\n";
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
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 0, 1.0F, 60.0F},
  };

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << name << " station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
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

  seat_aoi::ExternalTrigger trigger;
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
                      config.light_channels[2].current_percent == 55.0F &&
                      config.image_save.cleanup_enabled &&
                      config.image_save.cleanup_min_free_ratio == 0.20F;
  if (!passed) {
    std::cerr << "runtime light channel config did not parse expected values\n";
  }
  return passed;
}

bool test_image_save_path_uses_date_directory() {
  seat_aoi::ImageSaveConfig config;
  config.enabled = true;
  config.root_dir = "images";
  seat_aoi::CapturedFrame frame;
  frame.meta.timestamp_us = 1234567;
  frame.meta.light_index = 2;
  seat_aoi::copy_cstr(frame.meta.camera_id, "TOP/BACK");

  const std::string path =
      seat_aoi::build_original_image_path(config, "20260619", "SEAT:001", frame);

  const bool passed =
      path.find("images") != std::string::npos &&
      path.find("20260619") != std::string::npos &&
      path.find("SEAT_001") != std::string::npos &&
      path.find("TOP_BACK_1234567_L2_original.pgm") != std::string::npos;
  if (!passed) {
    std::cerr << "image save path did not include sanitized date/seat/camera: " << path
              << "\n";
  }
  return passed;
}

bool test_image_save_cleanup_removes_files_without_deleting_date_dirs() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_image_cleanup_" + std::to_string(seat_aoi::now_us()));
  constexpr std::size_t kOldestFileBytes = 16U * 1024U * 1024U;
  std::filesystem::create_directories(root / "20250101" / "OLD_SEAT");
  std::filesystem::create_directories(root / "20260619" / "CURRENT_SEAT");
  std::filesystem::create_directories(root / "misc");
  {
    std::ofstream oldest(root / "20250101" / "OLD_SEAT" / "oldest.pgm", std::ios::binary);
    const std::string chunk(1024U * 1024U, '\0');
    for (std::size_t written = 0; written < kOldestFileBytes; written += chunk.size()) {
      oldest.write(chunk.data(), static_cast<std::streamsize>(chunk.size()));
    }
    std::ofstream(root / "20250101" / "OLD_SEAT" / "newer.pgm") << "newer";
    std::ofstream(root / "20260619" / "CURRENT_SEAT" / "current.pgm") << "current";
    std::ofstream(root / "misc" / "keep.txt") << "keep";
  }
  const auto base_time = std::filesystem::file_time_type::clock::now();
  std::filesystem::last_write_time(root / "20250101" / "OLD_SEAT" / "oldest.pgm",
                                   base_time - std::chrono::hours(4));
  std::filesystem::last_write_time(root / "20250101" / "OLD_SEAT" / "newer.pgm",
                                   base_time - std::chrono::hours(3));
  std::filesystem::last_write_time(root / "20260619" / "CURRENT_SEAT" / "current.pgm",
                                   base_time - std::chrono::hours(1));

  seat_aoi::ImageSaveConfig config;
  config.enabled = true;
  config.save_original = true;
  config.cleanup_enabled = true;
  config.root_dir = root.string();
  config.cleanup_min_free_ratio = 1.0F;
  std::string message;
  const bool ok = seat_aoi::cleanup_old_image_data_if_needed(config, &message);

  const bool passed = ok &&
                      !std::filesystem::exists(root / "20250101" / "OLD_SEAT" / "oldest.pgm") &&
                      !std::filesystem::exists(root / "20250101" / "OLD_SEAT" / "newer.pgm") &&
                      !std::filesystem::exists(root / "20260619" / "CURRENT_SEAT" / "current.pgm") &&
                      std::filesystem::exists(root / "20250101") &&
                      std::filesystem::exists(root / "20260619") &&
                      std::filesystem::exists(root / "misc" / "keep.txt");
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  if (!passed) {
    std::cerr << "image cleanup did not remove files while keeping date roots: " << message << "\n";
  }
  return passed;
}

bool test_explicit_camera_config_replaces_defaults() {
  const std::string path =
      (std::filesystem::temp_directory_path() /
       ("seat_aoi_single_camera_config_" + std::to_string(seat_aoi::now_us()) + ".conf"))
          .string();
  {
    std::ofstream out(path);
    out << "hardware_mode=lab\n"
        << "signal.backend=manual_trigger\n"
        << "camera.backend=simulated\n"
        << "light.backend=simulated\n"
        << "slot_count=4\n"
        << "frame_slot_size=67108864\n"
        << "result_slot_size=65536\n"
        << "publish_timeout_ms=1000\n"
        << "detector_timeout_ms=5000\n"
        << "trigger_timeout_ms=1000\n"
        << "camera_timeout_ms=300\n"
        << "light_timeout_ms=300\n"
        << "warning_recheck_threshold=3\n"
        << "critical_recheck_threshold=5\n"
        << "max_jobs=1\n"
        << "recipe_id=seat_a_black_leather_production_v1\n"
        << "capture_mode=fixed_camera\n"
        << "light_order=1,2,3,4\n"
        << "trace_root=trace\n"
        << "signal.station_id=LAB_AOI_01\n"
        << "signal.default_sku=seat_a_black_leather\n"
        << "camera.0.camera_id=TOP_BACK\n"
        << "camera.0.serial_number=MVCH120_TEST_SN\n"
        << "camera.0.calibration_id=calib/top_back_production_v1\n"
        << "camera.0.width=4096\n"
        << "camera.0.height=3072\n"
        << "camera.0.channels=1\n"
        << "camera.0.pixel_format=Mono8\n"
        << "camera.0.trigger_line=Line0\n"
        << "camera.0.exposure_output_line=Line1\n"
        << "camera.0.buffer_count=8\n"
        << "light.device_id=FL-ACDH-20048-4\n"
        << "light.trigger_input_line=TriggerIn1\n"
        << "light.1.physical_channel=1\n"
        << "light.1.exposure_us=800\n"
        << "light.1.strobe_width_us=700\n"
        << "light.1.trigger_delay_us=10\n"
        << "light.1.gain=1.0\n"
        << "light.1.current_percent=60\n"
        << "light.2.physical_channel=2\n"
        << "light.2.exposure_us=800\n"
        << "light.2.strobe_width_us=700\n"
        << "light.2.trigger_delay_us=10\n"
        << "light.2.gain=1.0\n"
        << "light.2.current_percent=60\n"
        << "light.3.physical_channel=3\n"
        << "light.3.exposure_us=800\n"
        << "light.3.strobe_width_us=650\n"
        << "light.3.trigger_delay_us=10\n"
        << "light.3.gain=1.0\n"
        << "light.3.current_percent=55\n"
        << "light.4.physical_channel=4\n"
        << "light.4.exposure_us=800\n"
        << "light.4.strobe_width_us=650\n"
        << "light.4.trigger_delay_us=10\n"
        << "light.4.gain=1.0\n"
        << "light.4.current_percent=55\n";
  }
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  const bool ok = seat_aoi::load_station_runtime_config(path, &config, &error);
  std::remove(path.c_str());
  const bool passed = ok && config.cameras.size() == 1 &&
                      config.cameras[0].camera_index == 0 &&
                      config.cameras[0].width == 4096 &&
                      config.cameras[0].height == 3072;
  if (!passed) {
    std::cerr << "explicit camera config did not replace defaults: " << error
              << " camera_count=" << config.cameras.size() << "\n";
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
  config.signal.backend = seat_aoi::HardwareBackend::ExternalSignal;
  config.camera_backend = seat_aoi::HardwareBackend::HikrobotMvs;
  config.lights.emplace_back();
  config.lights[0].backend = seat_aoi::HardwareBackend::SerialAscii;
  config.frame_slot_size = 64 * 1024 * 1024;
  config.signal.station_id = "LINE1_AOI_01";
  config.signal.default_seat_id = "EXTERNAL_SEAT";
  config.signal.default_sku = "seat_a_black_leather";
  config.lights[0].device_id = "STROBE_01";
  config.lights[0].serial_port = "/dev/ttyUSB0";
  config.lights[0].baud_rate = 115200;
  config.lights[0].trigger_input_line = "TRIG_IN1";
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

bool test_lab_manual_trigger_config_validates() {
  auto config = make_filled_production_runtime_config();
  config.hardware_mode = seat_aoi::HardwareMode::Lab;
  config.signal.backend = seat_aoi::HardwareBackend::ManualTrigger;
  config.signal.station_id = "LAB_AOI_01";
  config.signal.default_sku = "seat_a_black_leather";
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  if (!ok) {
    std::cerr << "lab manual trigger config did not validate: " << error << "\n";
  }
  return ok;
}

bool test_production_rejects_manual_trigger_backend() {
  auto config = make_filled_production_runtime_config();
  config.signal.backend = seat_aoi::HardwareBackend::ManualTrigger;
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("manual_trigger") != std::string::npos;
  if (!passed) {
    std::cerr << "production manual trigger was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_manual_trigger_signal_client_generates_trigger() {
  seat_aoi::ManualSignalClient client;
  seat_aoi::SignalClientConfig config;
  config.station_id = "LAB_AOI_01";
  config.default_sku = "seat_a_black_leather";
  if (!client.initialize(config)) {
    std::cerr << "manual trigger client did not initialize\n";
    return false;
  }
  seat_aoi::ExternalTrigger trigger;
  std::string error;
  const bool ok = client.wait_trigger(&trigger, 100, &error);
  const bool sent = client.publish_result(
      trigger, 1, seat_aoi::InspectionDecision::Recheck, 100, &error);
  const bool passed = ok && sent && trigger.trigger_id == 9000 &&
                      trigger.seat_id == "LAB_AOI_01_MANUAL_SEAT_9000" &&
                      trigger.sku == "seat_a_black_leather" &&
                      client.get_health().ok;
  if (!passed) {
    std::cerr << "manual trigger client produced unexpected trigger: "
              << trigger.trigger_id << " " << trigger.seat_id << " " << trigger.sku
              << " error=" << error << "\n";
  }
  return passed;
}

bool test_hikrobot_backend_fails_without_sdk_when_not_compiled() {
#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
  return true;
#else
  auto camera = seat_aoi::create_camera(seat_aoi::HardwareBackend::HikrobotMvs);
  seat_aoi::CameraConfig config;
  config.camera_id = "TOP_BACK";
  config.serial_number = "MV-CH120-20GC-TEST";
  config.calibration_id = "calib/top_back_production_v1";
  config.width = 4096;
  config.height = 3072;
  config.channels = 1;
  config.pixel_format = "Mono8";
  config.trigger_line = "Line0";
  config.exposure_output_line = "Line1";
  const bool initialized = camera->initialize(config);
  const auto health = camera->get_health();
  const bool passed = !initialized && !health.ok &&
                      health.message.find("Hikrobot MVS SDK 未启用") != std::string::npos;
  if (!passed) {
    std::cerr << "hikrobot_mvs backend did not fail clearly without SDK: "
              << health.message << "\n";
  }
  return passed;
#endif
}

bool test_robot_flyshot_runtime_config_parses() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  bool ok = seat_aoi::load_station_runtime_config(
      "cpp_controller/config/station_runtime.robot_flyshot.example.conf", &config, &error);
  if (!ok) {
    error.clear();
    ok = seat_aoi::load_station_runtime_config(
        "config/station_runtime.robot_flyshot.example.conf", &config, &error);
  }
  const bool passed = ok &&
                      config.capture_mode == seat_aoi::CaptureMode::RobotFlyshot &&
                      config.cameras.size() >= 1 &&
                      config.capture_views.size() == 2 &&
                      config.capture_views[0].pose_id == "T1_BACKREST" &&
                      config.capture_views[1].pose_id == "T2_CUSHION" &&
                      config.capture_views[0].camera_index == 0 &&
                      config.capture_views[1].camera_id == "EYE_IN_HAND";
  if (!passed) {
    std::cerr << "robot_flyshot runtime config did not parse expected values: " << error << "\n";
  }
  return passed;
}

bool test_robot_flyshot_production_template_rejects_todo_placeholders() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  bool ok = seat_aoi::load_station_runtime_config(
      "cpp_controller/config/station_runtime.robot_flyshot.production.example.conf", &config, &error);
  if (ok) {
    std::cerr << "robot flyshot production template with TODO placeholders unexpectedly passed\n";
    return false;
  }
  if (error.empty()) {
    error.clear();
    ok = seat_aoi::load_station_runtime_config(
        "config/station_runtime.robot_flyshot.production.example.conf", &config, &error);
    if (ok) {
      std::cerr << "robot flyshot production template with TODO placeholders unexpectedly passed\n";
      return false;
    }
  }
  const bool passed = error.find("TODO") != std::string::npos ||
                      error.find("占位") != std::string::npos;
  if (!passed) {
    std::cerr << "robot flyshot production template rejection did not mention placeholder: "
              << error << "\n";
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

bool test_frame_slot_size_accounts_for_pixel_format_bytes() {
  seat_aoi::StationRuntimeConfig config;
  config.cameras = {
      seat_aoi::RuntimeCameraConfig{0, "TOP_BACK", "", "calib/simulated_v1", 64, 48, 1, "Mono16", "", "", 8, false},
  };
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 0, 1.0F, 60.0F},
  };
  config.frame_slot_size =
      static_cast<std::uint32_t>(seat_aoi::frame_slot_image_offset(1) + 64U * 48U);
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("frame_slot_size") != std::string::npos;
  if (!passed) {
    std::cerr << "frame slot size did not account for Mono16 bytes: " << error << "\n";
  }
  return passed;
}

bool test_health_monitor_escalates_after_consecutive_rechecks() {
  seat_aoi::StationHealthMonitor health;
  health.configure(2, 3);
  health.transition_to(seat_aoi::StationState::Ready, "ready");
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::QualityFailed,
                       "quality 1");
  const auto warning = health.snapshot();
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::QualityFailed,
                       "quality 2");
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::QualityFailed,
                       "quality 3");
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

bool test_detector_timeout_fault_blocks_next_trigger() {
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
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 0, 1.0F, 60.0F},
  };

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "detector timeout station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7011;
  trigger.seat_id = "SIM_TIMEOUT_BLOCK";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  const auto fault_snapshot = station.health_snapshot();
  seat_aoi::ExternalTrigger next_trigger;
  std::string error;
  const bool can_wait = station.wait_for_trigger(&next_trigger, &error);
  station.cleanup_shared_memory();

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::DetectorTimeout &&
      fault_snapshot.state == seat_aoi::StationState::Fault &&
      !can_wait &&
      error.find("detector result timeout") != std::string::npos;
  if (!passed) {
    std::cerr << "detector timeout did not block next trigger: " << error << "\n";
  }
  return passed;
}

bool write_detector_result_slot(std::uint64_t sequence_id,
                                std::uint64_t trigger_id,
                                const std::string& seat_id,
                                std::uint32_t slot_count,
                                std::uint32_t result_slot_size,
                                seat_aoi::InspectionDecision decision,
                                std::uint32_t quality_pass,
                                seat_aoi::ErrorCode error_code,
                                std::uint32_t defect_count) {
  const std::size_t total_size =
      seat_aoi::shared_memory_total_size(slot_count, result_slot_size);
  const auto open_deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
  seat_aoi::SharedMemory shm;
  while (std::chrono::steady_clock::now() < open_deadline) {
    if (shm.open_existing(seat_aoi::kResultShmName, total_size)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  if (!shm.is_open()) {
    std::cerr << "failed to open result shm for injected detector result\n";
    return false;
  }
  auto* base = slot_base(shm.data(), 0, result_slot_size);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(200);
  while (std::chrono::steady_clock::now() < deadline) {
    std::uint32_t expected = static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
    if (slot->state.compare_exchange_strong(
            expected,
            static_cast<std::uint32_t>(seat_aoi::SlotState::Writing),
            std::memory_order_acq_rel)) {
      std::memset(base + sizeof(std::uint32_t),
                  0,
                  result_slot_size - sizeof(std::uint32_t));
      const std::size_t defect_bytes =
          static_cast<std::size_t>(defect_count) * sizeof(seat_aoi::DefectResultMeta);
      slot->sequence_id = sequence_id;
      slot->payload_size = seat_aoi::result_slot_defects_offset() + defect_bytes;
      slot->defect_count = defect_count;
      slot->reserved = 0;
      slot->result_meta.sequence_id = sequence_id;
      slot->result_meta.trigger_id = trigger_id;
      seat_aoi::copy_cstr(slot->result_meta.seat_id, seat_id);
      slot->result_meta.decision = static_cast<std::uint32_t>(decision);
      slot->result_meta.defect_count = defect_count;
      slot->result_meta.quality_pass = quality_pass;
      slot->result_meta.error_code = static_cast<std::uint32_t>(error_code);
      slot->result_meta.elapsed_ms = 1.0F;
      slot->result_meta.reserved = 0;
      slot->payload_crc32 =
          seat_aoi::crc32(base + seat_aoi::result_slot_defects_offset(), defect_bytes);
      slot->header_crc32 = 0;
      slot->header_crc32 = result_header_crc(slot);
      slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                        std::memory_order_release);
      shm.close();
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  shm.close();
  std::cerr << "result shm slot did not become empty for injected detector result\n";
  return false;
}

bool test_detector_ng_with_quality_failure_is_rechecked() {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.publish_timeout_ms = 50;
  config.detector_timeout_ms = 500;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "invalid NG station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7012;
  trigger.seat_id = "SIM_INVALID_NG";
  trigger.sku = "seat_a_black_leather";
  const std::uint32_t slot_count = config.slot_count;
  const std::uint32_t result_slot_size = config.result_slot_size;
  std::thread detector_thread([&trigger, slot_count, result_slot_size]() {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    (void)write_detector_result_slot(1,
                                     trigger.trigger_id,
                                     trigger.seat_id,
                                     slot_count,
                                     result_slot_size,
                                     seat_aoi::InspectionDecision::NG,
                                     0,
                                     seat_aoi::ErrorCode::QualityFailed,
                                     1);
  });
  const auto result = station.inspect_one_seat(trigger);
  detector_thread.join();
  station.cleanup_shared_memory();

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::InvalidPayload &&
      result.meta.quality_pass == 0;
  if (!passed) {
    std::cerr << "detector NG with quality failure was not converted to RECHECK: decision="
              << result.meta.decision << " error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_station_writes_detector_timeout_event_log() {
  const auto trace_root = std::filesystem::temp_directory_path() /
                          ("seat_aoi_cpp_event_log_test_" +
                           std::to_string(seat_aoi::now_us()));
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
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 0, 1.0F, 60.0F},
  };
  config.trace_root = trace_root.string();

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "event log station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
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
  auto signal = seat_aoi::create_signal_client(seat_aoi::HardwareBackend::ModbusTcp);
  seat_aoi::SignalClientConfig config;
  const bool ok = signal->initialize(config);
  const auto health = signal->get_health();
  const bool passed = !ok && !health.ok &&
                      health.message.find("尚未链接真实硬件驱动") != std::string::npos;
  if (!passed) {
    std::cerr << "unsupported production backend did not fail fast: "
              << health.message << "\n";
    return false;
  }
  return true;
}

bool test_shared_memory_existing_size_mismatch_fails() {
  constexpr const char* kName = "/seat_aoi_shm_size_mismatch";
  seat_aoi::SharedMemory first;
  if (!first.create_or_open(kName, 4096, true)) {
    std::cerr << "initial shm create failed\n";
    return false;
  }
  seat_aoi::SharedMemory second;
  const bool ok = second.create_or_open(kName, 8192, false);
  second.close();
  first.unlink_name();
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "existing shm size mismatch did not fail\n";
  }
  return passed;
}

bool test_invalid_bool_config_rejected() {
  const std::string path =
      (std::filesystem::temp_directory_path() /
       ("seat_aoi_invalid_bool_config_" + std::to_string(seat_aoi::now_us()) + ".conf"))
          .string();
  {
    std::ofstream output(path);
    output << "reset_shared_memory=flase\n";
  }
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  const bool ok = seat_aoi::load_station_runtime_config(path, &config, &error);
  std::remove(path.c_str());
  const bool passed = !ok && error.find("reset_shared_memory") != std::string::npos &&
                      error.find("布尔值") != std::string::npos;
  if (!passed) {
    std::cerr << "invalid bool config was not rejected: " << error << "\n";
  }
  return passed;
}

}  // namespace

int main() {
  if (!test_frame_ring_skips_blocked_slot()) {
    return 1;
  }
  if (!test_result_ring_returns_crc_error_immediately()) {
    return 1;
  }
  if (!test_result_ring_rejects_payload_size_mismatch()) {
    return 1;
  }
  if (!test_result_ring_reclaims_stale_and_bad_slots()) {
    return 1;
  }
  if (!test_result_ring_returns_current_bad_slot_error()) {
    return 1;
  }
  if (!test_ring_layout_mismatch_fails_without_reset()) {
    return 1;
  }
  if (!test_signal_trigger_timeout_fails_closed()) {
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
  if (!test_image_save_path_uses_date_directory()) {
    return 1;
  }
  if (!test_image_save_cleanup_removes_files_without_deleting_date_dirs()) {
    return 1;
  }
  if (!test_explicit_camera_config_replaces_defaults()) {
    return 1;
  }
  if (!test_production_template_rejects_todo_placeholders()) {
    return 1;
  }
  if (!test_filled_production_config_validates()) {
    return 1;
  }
  if (!test_lab_manual_trigger_config_validates()) {
    return 1;
  }
  if (!test_production_rejects_manual_trigger_backend()) {
    return 1;
  }
  if (!test_manual_trigger_signal_client_generates_trigger()) {
    return 1;
  }
  if (!test_hikrobot_backend_fails_without_sdk_when_not_compiled()) {
    return 1;
  }
  if (!test_robot_flyshot_runtime_config_parses()) {
    return 1;
  }
  if (!test_robot_flyshot_production_template_rejects_todo_placeholders()) {
    return 1;
  }
  if (!test_strobe_width_larger_than_exposure_rejected()) {
    return 1;
  }
  if (!test_invalid_health_threshold_rejected()) {
    return 1;
  }
  if (!test_frame_slot_size_accounts_for_pixel_format_bytes()) {
    return 1;
  }
  if (!test_health_monitor_escalates_after_consecutive_rechecks()) {
    return 1;
  }
  if (!test_detector_timeout_fault_blocks_next_trigger()) {
    return 1;
  }
  if (!test_detector_ng_with_quality_failure_is_rechecked()) {
    return 1;
  }
  if (!test_station_writes_detector_timeout_event_log()) {
    return 1;
  }
  if (!test_unsupported_production_backend_fails_fast()) {
    return 1;
  }
  if (!test_shared_memory_existing_size_mismatch_fails()) {
    return 1;
  }
  if (!test_invalid_bool_config_rejected()) {
    return 1;
  }
  std::cout << "ipc safety checks passed\n";
  return 0;
}
