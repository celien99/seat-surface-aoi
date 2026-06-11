#pragma once

#include <atomic>
#include <cstddef>
#include <cstdint>

#include "common/inspection_types.hpp"

namespace seat_aoi {

inline constexpr std::uint32_t kShmProtocolMagic = 0x53414F49;  // "SAOI"
inline constexpr std::uint32_t kShmProtocolVersion = 2;
inline constexpr std::uint32_t kDefaultSlotCount = 4;
inline constexpr std::uint32_t kDefaultFrameSlotSize = 16 * 1024 * 1024;
inline constexpr std::uint32_t kDefaultResultSlotSize = 64 * 1024;

inline constexpr const char* kFrameShmName = "/seat_aoi_cpp_to_py_frames_v1";
inline constexpr const char* kResultShmName = "/seat_aoi_py_to_cpp_results_v1";

enum class SlotState : std::uint32_t {
  Empty = 0,
  Writing = 1,
  Ready = 2,
  Reading = 3,
  Corrupted = 4,
  Timeout = 5,
};

#pragma pack(push, 1)

struct ShmHeader {
  std::uint32_t magic;
  std::uint32_t version;
  std::uint32_t slot_count;
  std::uint32_t slot_size;
  std::uint64_t write_index;
  std::uint64_t read_index;
  std::uint64_t heartbeat;
};

struct FrameSlotHeader {
  std::atomic<std::uint32_t> state;
  std::uint64_t sequence_id;
  std::uint64_t payload_size;
  std::uint32_t header_crc32;
  std::uint32_t payload_crc32;
  std::uint32_t frame_meta_count;
  std::uint32_t reserved;
  SeatJobMeta job_meta;
};

struct ResultSlotHeader {
  std::atomic<std::uint32_t> state;
  std::uint64_t sequence_id;
  std::uint64_t payload_size;
  std::uint32_t header_crc32;
  std::uint32_t payload_crc32;
  std::uint32_t defect_count;
  std::uint32_t reserved;
  InspectionResultMeta result_meta;
};

#pragma pack(pop)

static_assert(sizeof(ShmHeader) == 40, "Unexpected ShmHeader size");
static_assert(sizeof(FrameSlotHeader) == 268, "Unexpected FrameSlotHeader size");
static_assert(sizeof(ResultSlotHeader) == 140, "Unexpected ResultSlotHeader size");

inline std::size_t frame_slot_meta_offset() {
  return sizeof(FrameSlotHeader);
}

inline std::size_t frame_slot_image_offset(std::uint32_t frame_meta_count) {
  return sizeof(FrameSlotHeader) + sizeof(LightFrameMeta) * frame_meta_count;
}

inline std::size_t result_slot_defects_offset() {
  return sizeof(ResultSlotHeader);
}

inline std::size_t shared_memory_total_size(std::uint32_t slot_count,
                                            std::uint32_t slot_size) {
  return sizeof(ShmHeader) + static_cast<std::size_t>(slot_count) * slot_size;
}

}  // namespace seat_aoi
