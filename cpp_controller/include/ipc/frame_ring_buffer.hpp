#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "common/inspection_types.hpp"
#include "ipc/shared_memory.hpp"
#include "ipc/shm_protocol.hpp"

namespace seat_aoi {

struct CapturedFrame {
  LightFrameMeta meta{};
  std::vector<std::uint8_t> bytes;
};

struct SeatImageBundle {
  SeatJobMeta job_meta{};
  std::vector<CapturedFrame> frames;
};

class FrameRingBuffer {
public:
  bool initialize(const std::string& name,
                  std::uint32_t slot_count,
                  std::uint32_t slot_size,
                  bool reset);

  bool publish(const SeatImageBundle& bundle,
               int timeout_ms,
               std::uint64_t* out_sequence_id,
               std::string* error_message);

  void close();
  void unlink_name();
  ShmHeader* header();

private:
  FrameSlotHeader* slot_header(std::uint32_t slot_index);
  std::uint8_t* slot_base(std::uint32_t slot_index);
  bool validate_bundle(const SeatImageBundle& bundle,
                       std::size_t* payload_size,
                       std::string* error_message) const;

  SharedMemory shm_;
  std::uint32_t slot_count_ = 0;
  std::uint32_t slot_size_ = 0;
};

}  // namespace seat_aoi

