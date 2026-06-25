#pragma once

#include <cstdint>
#include <filesystem>
#include <map>
#include <string>
#include <vector>

namespace seat_aoi {

struct ReplayCaptureFile {
  std::uint32_t light_index = 0;
  std::uint64_t timestamp_us = 0;
  std::filesystem::path path;
};

struct ReplayCaptureGroup {
  std::uint32_t sample_index = 0;
  std::map<std::uint32_t, ReplayCaptureFile> files_by_light;
  bool has_duplicate_light = false;
};

bool is_complete_replay_group(const ReplayCaptureGroup& group,
                              const std::vector<std::uint32_t>& required_lights);

std::vector<std::uint32_t> complete_replay_sample_indices(
    const std::vector<ReplayCaptureGroup>& groups,
    const std::vector<std::uint32_t>& required_lights);

std::vector<ReplayCaptureGroup> scan_replay_capture_groups(
    const std::string& replay_root,
    const std::string& camera_id,
    const std::vector<std::uint32_t>& required_lights,
    std::string* error_message);

}  // namespace seat_aoi
