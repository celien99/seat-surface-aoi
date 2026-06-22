#include "control/image_writer.hpp"

#include <algorithm>
#include <cctype>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <system_error>
#include <vector>

#define STB_IMAGE_WRITE_IMPLEMENTATION
#include "common/stb_image_write.h"

#include "common/string_utils.hpp"
#include "control/station_runtime_config.hpp"
#include "ipc/frame_ring_buffer.hpp"
#include "ipc/shm_protocol.hpp"

namespace seat_aoi {

namespace {

std::string safe_path_name(const std::string& value) {
  std::string out;
  out.reserve(value.size());
  for (const unsigned char ch : value) {
    if (std::isalnum(ch) || ch == '-' || ch == '_') {
      out.push_back(static_cast<char>(ch));
    } else {
      out.push_back('_');
    }
  }
  return out.empty() ? "unknown" : out;
}

bool is_date_dir_name(const std::string& value) {
  if (value.size() != 8) {
    return false;
  }
  return std::all_of(value.begin(), value.end(), [](unsigned char ch) {
    return std::isdigit(ch) != 0;
  });
}

struct ImageFileEntry {
  std::filesystem::path path;
  std::filesystem::file_time_type write_time;
};

struct StorageCleanupStats {
  std::uintmax_t removed_files = 0;
  double free_ratio_after = 1.0;
};

double free_ratio(const std::filesystem::space_info& info) {
  if (info.capacity == 0U) {
    return 1.0;
  }
  return static_cast<double>(info.available) / static_cast<double>(info.capacity);
}

bool cleanup_date_tree_if_needed(const std::filesystem::path& root,
                                 float cleanup_min_free_ratio,
                                 StorageCleanupStats* stats,
                                 std::string* error) {
  std::error_code ec;
  std::filesystem::create_directories(root, ec);
  if (ec) {
    if (error != nullptr) {
      *error = "创建存储根目录失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }

  auto space = std::filesystem::space(root, ec);
  if (ec) {
    if (error != nullptr) {
      *error = "读取存储磁盘容量失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }
  const double min_free_ratio = static_cast<double>(cleanup_min_free_ratio);
  if (stats != nullptr) {
    stats->free_ratio_after = free_ratio(space);
  }
  if (free_ratio(space) >= min_free_ratio) {
    return true;
  }

  std::vector<ImageFileEntry> old_files;
  for (const auto& entry : std::filesystem::directory_iterator(root, ec)) {
    if (ec) {
      break;
    }
    if (!entry.is_directory(ec)) {
      continue;
    }
    const std::string name = entry.path().filename().string();
    if (!is_date_dir_name(name)) {
      continue;
    }
    std::error_code walk_ec;
    for (const auto& file_entry : std::filesystem::recursive_directory_iterator(entry.path(), walk_ec)) {
      if (walk_ec) {
        break;
      }
      if (!file_entry.is_regular_file(walk_ec)) {
        continue;
      }
      std::error_code time_ec;
      const auto write_time = file_entry.last_write_time(time_ec);
      if (time_ec) {
        continue;
      }
      old_files.push_back(ImageFileEntry{file_entry.path(), write_time});
    }
    if (walk_ec) {
      if (error != nullptr) {
        *error = "扫描业务文件失败: " + entry.path().string() + " error=" + walk_ec.message();
      }
      return false;
    }
  }
  if (ec) {
    if (error != nullptr) {
      *error = "扫描业务目录失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }
  std::sort(old_files.begin(), old_files.end(), [](const ImageFileEntry& lhs, const ImageFileEntry& rhs) {
    if (lhs.write_time == rhs.write_time) {
      return lhs.path.string() < rhs.path.string();
    }
    return lhs.write_time < rhs.write_time;
  });

  for (const auto& file : old_files) {
    std::error_code remove_ec;
    const bool removed = std::filesystem::remove(file.path, remove_ec);
    if (remove_ec && std::filesystem::exists(file.path)) {
      if (error != nullptr) {
        *error = "删除旧业务文件失败: " + file.path.string() + " error=" + remove_ec.message();
      }
      return false;
    }
    if (removed && stats != nullptr) {
      ++stats->removed_files;
    }
    space = std::filesystem::space(root, ec);
    if (ec) {
      if (error != nullptr) {
        *error = "重新读取存储磁盘容量失败: " + root.string() + " error=" + ec.message();
      }
      return false;
    }
    if (stats != nullptr) {
      stats->free_ratio_after = free_ratio(space);
    }
    if (free_ratio(space) >= min_free_ratio) {
      break;
    }
  }

  std::vector<std::filesystem::path> empty_dirs;
  for (const auto& entry : std::filesystem::directory_iterator(root, ec)) {
    if (ec) {
      break;
    }
    if (!entry.is_directory(ec) || !is_date_dir_name(entry.path().filename().string())) {
      continue;
    }
    std::error_code walk_ec;
    for (const auto& dir_entry : std::filesystem::recursive_directory_iterator(entry.path(), walk_ec)) {
      if (walk_ec) {
        break;
      }
      if (dir_entry.is_directory(walk_ec)) {
        empty_dirs.push_back(dir_entry.path());
      }
    }
  }
  std::sort(empty_dirs.begin(), empty_dirs.end(), [](const auto& lhs, const auto& rhs) {
    return lhs.string().size() > rhs.string().size();
  });
  for (const auto& dir : empty_dirs) {
    std::error_code empty_ec;
    if (std::filesystem::is_empty(dir, empty_ec)) {
      std::error_code remove_empty_ec;
      std::filesystem::remove(dir, remove_empty_ec);
    }
  }

  return true;
}

bool has_required_free_ratio(const std::filesystem::path& root,
                             float min_free_ratio,
                             double* out_ratio,
                             std::string* error) {
  std::error_code ec;
  std::filesystem::create_directories(root, ec);
  if (ec) {
    if (error != nullptr) {
      *error = "创建存储根目录失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }
  const auto space = std::filesystem::space(root, ec);
  if (ec) {
    if (error != nullptr) {
      *error = "读取存储磁盘容量失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }
  const double ratio = free_ratio(space);
  if (out_ratio != nullptr) {
    *out_ratio = ratio;
  }
  return ratio >= static_cast<double>(min_free_ratio);
}

}  // namespace

bool make_dirs(const std::string& path, std::string* error) {
  if (path.empty()) return true;

  std::error_code ec;
  if (std::filesystem::is_directory(path, ec)) {
    return true;
  }
  if (std::filesystem::create_directories(path, ec) || std::filesystem::is_directory(path, ec)) {
    return true;
  }
  if (error != nullptr) {
    *error = "mkdir failed: " + path + " error=" + ec.message();
  }
  return false;
}

std::string image_save_date_dir() {
  const std::time_t now = std::time(nullptr);
  std::tm local_time{};
#ifdef _WIN32
  localtime_s(&local_time, &now);
#else
  localtime_r(&now, &local_time);
#endif
  std::ostringstream out;
  out << std::put_time(&local_time, "%Y%m%d");
  return out.str();
}

std::string build_original_image_path(const ImageSaveConfig& config,
                                      const std::string& date_dir,
                                      const std::string& seat_id,
                                      const CapturedFrame& frame) {
  const std::string camera_id = fixed_cstr_to_string(frame.meta.camera_id, kStringIdSize);
  std::ostringstream filename;
  filename << safe_path_name(camera_id) << "_" << frame.meta.timestamp_us << "_L"
           << frame.meta.light_index << "_original.png";
  const std::filesystem::path path =
      std::filesystem::path(config.root_dir) / safe_path_name(date_dir) /
      safe_path_name(seat_id) / filename.str();
  return path.string();
}

bool cleanup_old_image_data_if_needed(const ImageSaveConfig& config,
                                      std::string* message) {
  if (!config.enabled || !config.save_original || !config.cleanup_enabled ||
      config.cleanup_min_free_ratio <= 0.0F) {
    return true;
  }

  const std::filesystem::path root(config.root_dir);
  StorageCleanupStats stats;
  std::string cleanup_error;
  if (!cleanup_date_tree_if_needed(root, config.cleanup_min_free_ratio, &stats, &cleanup_error)) {
    if (message != nullptr) {
      *message = cleanup_error;
    }
    return false;
  }
  if (message != nullptr && stats.removed_files > 0U) {
    std::ostringstream out;
    out << "图片磁盘可用容量低于 " << static_cast<int>(config.cleanup_min_free_ratio * 100.0F)
        << "%，已按时间清理最早图片文件 " << stats.removed_files
        << " 个，当前可用比例 " << std::fixed << std::setprecision(3) << stats.free_ratio_after;
    *message = out.str();
  } else if (message != nullptr &&
             stats.free_ratio_after < static_cast<double>(config.cleanup_min_free_ratio)) {
    std::ostringstream out;
    out << "图片磁盘可用容量低于 " << static_cast<int>(config.cleanup_min_free_ratio * 100.0F)
        << "%，但没有可清理的历史图片文件";
    *message = out.str();
  }
  return true;
}

bool cleanup_runtime_storage_if_needed(const ImageSaveConfig& config,
                                       const std::string& trace_root,
                                       std::string* message) {
  if (!config.cleanup_enabled || config.cleanup_min_free_ratio <= 0.0F) {
    return true;
  }
  std::vector<std::string> messages;
  const auto cleanup_one = [&](const std::filesystem::path& root, const char* label) -> bool {
    StorageCleanupStats stats;
    std::string error;
    if (!cleanup_date_tree_if_needed(root, config.cleanup_min_free_ratio, &stats, &error)) {
      if (message != nullptr) {
        *message = std::string(label) + "清理失败: " + error;
      }
      return false;
    }
    if (stats.removed_files > 0U) {
      std::ostringstream out;
      out << label << "低水位清理 " << stats.removed_files
          << " 个历史文件，当前可用比例 " << std::fixed << std::setprecision(3)
          << stats.free_ratio_after;
      messages.push_back(out.str());
    }
    return true;
  };
  if (config.enabled || std::filesystem::exists(config.root_dir)) {
    if (!cleanup_one(config.root_dir, "图片目录")) {
      return false;
    }
  }
  if (config.cleanup_trace_root && !trace_root.empty()) {
    if (!cleanup_one(trace_root, "trace目录")) {
      return false;
    }
  }
  if (message != nullptr && !messages.empty()) {
    std::ostringstream out;
    for (std::size_t index = 0; index < messages.size(); ++index) {
      if (index > 0) {
        out << "; ";
      }
      out << messages[index];
    }
    *message = out.str();
  }
  return true;
}

bool runtime_storage_has_required_free_ratio(const ImageSaveConfig& config,
                                             const std::string& trace_root,
                                             std::string* message) {
  if (!config.cleanup_enabled || config.cleanup_min_free_ratio <= 0.0F) {
    return true;
  }
  const auto check_one = [&](const std::filesystem::path& root, const char* label) -> bool {
    double ratio = 1.0;
    std::string error;
    if (!has_required_free_ratio(root, config.cleanup_min_free_ratio, &ratio, &error)) {
      if (message != nullptr) {
        std::ostringstream out;
        out << label << "可用容量比例 " << std::fixed << std::setprecision(3) << ratio
            << " 低于阈值 " << config.cleanup_min_free_ratio;
        if (!error.empty()) {
          out << ": " << error;
        }
        *message = out.str();
      }
      return false;
    }
    return true;
  };
  if ((config.enabled || std::filesystem::exists(config.root_dir)) &&
      !check_one(config.root_dir, "图片目录")) {
    return false;
  }
  if (config.cleanup_trace_root && !trace_root.empty() && !check_one(trace_root, "trace目录")) {
    return false;
  }
  return true;
}

bool write_png(const std::string& path,
               const std::vector<std::uint8_t>& bytes,
               std::uint32_t width,
               std::uint32_t height,
               std::string* error) {
  if (bytes.empty() || width == 0 || height == 0) {
    if (error != nullptr) *error = "write_png: invalid image data";
    return false;
  }

  // 确保目录存在
  const auto last_sep = path.find_last_of("/\\");
  if (last_sep != std::string::npos) {
    std::string dir_error;
    if (!make_dirs(path.substr(0, last_sep), &dir_error)) {
      std::cerr << "image_writer mkdir warning: " << dir_error << std::endl;
    }
  }

  const int stride = static_cast<int>(width);
  const int result = stbi_write_png(
      path.c_str(),
      static_cast<int>(width),
      static_cast<int>(height),
      1,  // Mono8 = 1 channel
      bytes.data(),
      stride);

  if (result == 0) {
    if (error != nullptr) *error = "write_png: stbi_write_png failed " + path;
    return false;
  }

  std::cout << "Image saved: " << path << " (" << bytes.size() << " bytes)"
            << std::endl;
  return true;
}

bool write_pgm(const std::string& path,
               const std::vector<std::uint8_t>& bytes,
               std::uint32_t width,
               std::uint32_t height,
               std::string* error) {
  if (bytes.empty() || width == 0 || height == 0) {
    if (error != nullptr) *error = "write_pgm: invalid image data";
    return false;
  }

  // 提取目录并创建
  const auto last_sep = path.find_last_of("/\\");
  if (last_sep != std::string::npos) {
    std::string dir_error;
    if (!make_dirs(path.substr(0, last_sep), &dir_error)) {
      std::cerr << "image_writer mkdir warning: " << dir_error << std::endl;
    }
  }

  std::ofstream file(path, std::ios::binary);
  if (!file.good()) {
    if (error != nullptr) *error = "write_pgm: cannot open " + path;
    return false;
  }

  file << "P5\n" << width << " " << height << "\n255\n";
  file.write(reinterpret_cast<const char*>(bytes.data()),
             static_cast<std::streamsize>(bytes.size()));
  file.close();

  if (!file.good()) {
    if (error != nullptr) *error = "write_pgm: write failed " + path;
    return false;
  }

  std::cout << "Image saved: " << path << " (" << bytes.size() << " bytes)"
            << std::endl;
  return true;
}

}  // namespace seat_aoi
