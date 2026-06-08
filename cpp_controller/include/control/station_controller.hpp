#pragma once

#include <cstdint>
#include <string>

#include "control/frame_assembler.hpp"
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
};

class StationController {
public:
  bool initialize(const StationConfig& config);
  InspectionResultPayload inspect_one_seat(const PlcTrigger& trigger);
  void cleanup_shared_memory();

private:
  Recipe load_recipe(const std::string& sku) const;
  InspectionResultPayload make_recheck_result(const PlcTrigger& trigger,
                                              std::uint64_t sequence_id,
                                              ErrorCode error_code,
                                              const std::string& message) const;
  void log_result(const InspectionResultPayload& result) const;

  StationConfig config_{};
  FrameRingBuffer frame_ring_;
  ResultRingBuffer result_ring_;
  FrameAssembler frame_assembler_;
  std::uint64_t next_sequence_id_ = 1;
};

}  // namespace seat_aoi

