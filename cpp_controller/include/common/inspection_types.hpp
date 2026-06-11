#pragma once

#include <cstddef>
#include <cstdint>

namespace seat_aoi {

inline constexpr std::uint32_t kStringIdSize = 64;
inline constexpr std::uint32_t kMaxFramesPerJob = 64;
inline constexpr std::uint32_t kMaxDefectsPerResult = 32;
inline constexpr std::uint32_t kMaxEvidenceLights = 8;

enum class PixelFormat : std::uint32_t {
  Mono8 = 1,
  Mono10 = 2,
  Mono12 = 3,
  Mono16 = 4,
  BayerRG8 = 10,
  BayerRG12 = 11,
  BGR8 = 20,
  RGB8 = 21,
};

enum class ColorOrder : std::uint32_t {
  Mono = 1,
  BGR = 2,
  RGB = 3,
  BayerRG = 4,
  BayerGB = 5,
  BayerGR = 6,
  BayerBG = 7,
};

enum class DTypeCode : std::uint32_t {
  UInt8 = 1,
  UInt16 = 2,
  Float32 = 3,
};

enum class InspectionDecision : std::uint32_t {
  OK = 1,
  NG = 2,
  Recheck = 3,
  Error = 4,
};

#pragma pack(push, 1)

struct LightFrameMeta {
  std::uint32_t camera_index;
  std::uint32_t pose_index;
  std::uint32_t light_index;
  std::uint32_t frame_index;
  std::uint32_t light_seq_index;
  std::uint32_t width;
  std::uint32_t height;
  std::uint32_t channels;
  std::uint32_t stride_bytes;
  std::uint32_t pixel_format;
  std::uint32_t bit_depth;
  std::uint32_t color_order;
  std::uint32_t dtype_code;
  std::uint64_t timestamp_us;
  std::uint64_t shot_id;
  std::uint64_t robot_timestamp_us;
  std::uint32_t exposure_us;
  float gain;
  float robot_tcp_xyz_mm[3];
  float robot_rpy_deg[3];
  char camera_id[kStringIdSize];
  char pose_id[kStringIdSize];
  char calibration_id[kStringIdSize];
  std::uint64_t image_offset;
  std::uint64_t image_size;
  std::uint32_t image_crc32;
  std::uint32_t reserved;
};

struct SeatJobMeta {
  std::uint64_t sequence_id;
  std::uint64_t trigger_id;
  char seat_id[kStringIdSize];
  char sku[kStringIdSize];
  char recipe_id[kStringIdSize];
  std::uint32_t view_count;
  std::uint32_t frame_count;
  std::uint32_t capture_mode;
  std::uint32_t reserved;
  std::uint64_t created_at_us;
};

struct DefectResultMeta {
  char defect_id[kStringIdSize];
  char class_name[kStringIdSize];
  char severity[kStringIdSize];
  std::uint32_t camera_index;
  char camera_id[kStringIdSize];
  char pose_id[kStringIdSize];
  char roi_name[kStringIdSize];
  std::int32_t bbox_xyxy[4];
  float score;
  std::uint32_t area_px;
  std::uint32_t evidence_light_count;
  std::uint32_t evidence_light_indices[kMaxEvidenceLights];
  std::int64_t mask_offset;
  std::uint32_t decision;
  std::uint32_t reserved;
};

struct InspectionResultMeta {
  std::uint64_t sequence_id;
  std::uint64_t trigger_id;
  char seat_id[kStringIdSize];
  std::uint32_t decision;
  std::uint32_t defect_count;
  std::uint32_t quality_pass;
  std::uint32_t error_code;
  float elapsed_ms;
  std::uint32_t reserved;
};

#pragma pack(pop)

static_assert(sizeof(LightFrameMeta) == 324, "Unexpected LightFrameMeta size");
static_assert(sizeof(SeatJobMeta) == 232, "Unexpected SeatJobMeta size");
static_assert(sizeof(DefectResultMeta) == 464, "Unexpected DefectResultMeta size");
static_assert(sizeof(InspectionResultMeta) == 104, "Unexpected InspectionResultMeta size");

}  // namespace seat_aoi
