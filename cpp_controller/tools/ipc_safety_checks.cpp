#include <array>
#include <cstring>
#include <iostream>
#include <string>
#include <vector>

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"
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

}  // namespace

int main() {
  if (!test_frame_ring_skips_blocked_slot()) {
    return 1;
  }
  if (!test_result_ring_returns_crc_error_immediately()) {
    return 1;
  }
  std::cout << "ipc safety checks passed\n";
  return 0;
}
