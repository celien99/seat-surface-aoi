#include "camera/replay_capture.hpp"

#include <algorithm>
#include <regex>
#include <set>
#include <sstream>

namespace seat_aoi {

namespace {

std::string regex_escape(const std::string& value) {
  static const std::regex special(R"([-[\]{}()*+?.,\^$|#\s])");
  return std::regex_replace(value, special, R"(\$&)");
}

bool has_required_light(const std::vector<std::uint32_t>& required_lights,
                        std::uint32_t light_index) {
  return std::find(required_lights.begin(), required_lights.end(), light_index) !=
         required_lights.end();
}

}  // namespace

bool is_complete_replay_group(const ReplayCaptureGroup& group,
                              const std::vector<std::uint32_t>& required_lights) {
  if (group.has_duplicate_light || required_lights.empty()) {
    return false;
  }
  for (const auto light_index : required_lights) {
    if (group.files_by_light.find(light_index) == group.files_by_light.end()) {
      return false;
    }
  }
  return true;
}

std::vector<std::uint32_t> complete_replay_sample_indices(
    const std::vector<ReplayCaptureGroup>& groups,
    const std::vector<std::uint32_t>& required_lights) {
  std::vector<std::uint32_t> sample_indices;
  for (const auto& group : groups) {
    if (is_complete_replay_group(group, required_lights)) {
      sample_indices.push_back(group.sample_index);
    }
  }
  return sample_indices;
}

std::vector<ReplayCaptureGroup> scan_replay_capture_groups(
    const std::string& replay_root,
    const std::string& camera_id,
    const std::vector<std::uint32_t>& required_lights,
    std::string* error_message) {
  namespace fs = std::filesystem;
  if (required_lights.empty()) {
    if (error_message != nullptr) {
      *error_message = "replay required lights is empty";
    }
    return {};
  }

  const fs::path root(replay_root);
  std::error_code ec;
  if (!fs::is_directory(root, ec)) {
    if (error_message != nullptr) {
      *error_message = "images_capture replay root is not a directory: " + replay_root;
    }
    return {};
  }

  const std::regex capture_re("^" + regex_escape(camera_id) +
                              R"(_(\d+)_L(\d+)_.*\.png$)",
                              std::regex_constants::icase);
  std::vector<ReplayCaptureFile> files;
  for (const auto& entry : fs::directory_iterator(root, ec)) {
    if (ec) {
      if (error_message != nullptr) {
        *error_message = "failed to scan replay root: " + ec.message();
      }
      return {};
    }
    if (!entry.is_regular_file(ec)) {
      continue;
    }
    std::smatch match;
    const auto filename = entry.path().filename().string();
    if (!std::regex_match(filename, match, capture_re)) {
      continue;
    }
    const auto light_index = static_cast<std::uint32_t>(std::stoul(match[2].str()));
    if (!has_required_light(required_lights, light_index)) {
      continue;
    }
    files.push_back(ReplayCaptureFile{light_index,
                                      static_cast<std::uint64_t>(std::stoull(match[1].str())),
                                      entry.path()});
  }
  if (files.empty()) {
    if (error_message != nullptr) {
      *error_message = "no replay PNG files found for camera_id=" + camera_id +
                       " root=" + replay_root;
    }
    return {};
  }

  std::sort(files.begin(), files.end(), [](const ReplayCaptureFile& lhs,
                                           const ReplayCaptureFile& rhs) {
    if (lhs.timestamp_us != rhs.timestamp_us) {
      return lhs.timestamp_us < rhs.timestamp_us;
    }
    if (lhs.light_index != rhs.light_index) {
      return lhs.light_index < rhs.light_index;
    }
    return lhs.path.string() < rhs.path.string();
  });

  std::vector<ReplayCaptureGroup> groups;
  ReplayCaptureGroup current;
  for (const auto& file : files) {
    if (file.light_index == required_lights.front() &&
        !current.files_by_light.empty()) {
      current.sample_index = static_cast<std::uint32_t>(groups.size() + 1U);
      groups.push_back(std::move(current));
      current = ReplayCaptureGroup{};
    }
    if (current.files_by_light.find(file.light_index) != current.files_by_light.end()) {
      current.has_duplicate_light = true;
    }
    current.files_by_light[file.light_index] = file;
  }
  if (!current.files_by_light.empty()) {
    current.sample_index = static_cast<std::uint32_t>(groups.size() + 1U);
    groups.push_back(std::move(current));
  }

  std::set<std::uint32_t> seen_lights;
  for (const auto& file : files) {
    seen_lights.insert(file.light_index);
  }
  for (const auto light_index : required_lights) {
    if (seen_lights.find(light_index) == seen_lights.end()) {
      std::ostringstream message;
      message << "replay missing required light_index=" << light_index
              << " camera_id=" << camera_id;
      if (error_message != nullptr) {
        *error_message = message.str();
      }
      return {};
    }
  }

  return groups;
}

}  // namespace seat_aoi
