#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "common/error_code.hpp"
#include "common/inspection_types.hpp"
#include "ipc/shared_memory.hpp"
#include "ipc/shm_protocol.hpp"

namespace seat_aoi {

struct InspectionResultPayload {
  InspectionResultMeta meta{};
  std::vector<DefectResultMeta> defects;
};

class ResultRingBuffer {
public:
  bool initialize(const std::string& name,
                  std::uint32_t slot_count,
                  std::uint32_t slot_size,
                  bool reset);

  bool wait_for_result(std::uint64_t sequence_id,
                       int timeout_ms,
                       InspectionResultPayload* out_result,
                       std::string* error_message);

  void close();
  void unlink_name();
  ShmHeader* header();

private:
  ResultSlotHeader* slot_header(std::uint32_t slot_index);
  std::uint8_t* slot_base(std::uint32_t slot_index);
  bool read_ready_slot(std::uint32_t slot_index,
                       std::uint64_t sequence_id,
                       InspectionResultPayload* out_result,
                       std::string* error_message);

  SharedMemory shm_;
  std::uint32_t slot_count_ = 0;
  std::uint32_t slot_size_ = 0;
};

}  // namespace seat_aoi

