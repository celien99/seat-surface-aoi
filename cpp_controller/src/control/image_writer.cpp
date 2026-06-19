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

double free_ratio(const std::filesystem::space_info& info) {
  if (info.capacity == 0U) {
    return 1.0;
  }
  return static_cast<double>(info.available) / static_cast<double>(info.capacity);
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
           << frame.meta.light_index << "_original.pgm";
  const std::filesystem::path path =
      std::filesystem::path(config.root_dir) / safe_path_name(date_dir) /
      safe_path_name(seat_id) / filename.str();
  return path.string();
}

bool cleanup_old_image_data_if_needed(const ImageSaveConfig& config,
                                      const std::string& current_date_dir,
                                      std::string* message) {
  if (!config.enabled || !config.save_original || !config.cleanup_enabled ||
      config.cleanup_min_free_ratio <= 0.0F) {
    return true;
  }

  std::error_code ec;
  const std::filesystem::path root(config.root_dir);
  std::filesystem::create_directories(root, ec);
  if (ec) {
    if (message != nullptr) {
      *message = "创建图片根目录失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }

  auto space = std::filesystem::space(root, ec);
  if (ec) {
    if (message != nullptr) {
      *message = "读取图片磁盘容量失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }
  const double min_free_ratio = static_cast<double>(config.cleanup_min_free_ratio);
  if (free_ratio(space) >= min_free_ratio) {
    return true;
  }

  std::vector<std::filesystem::path> old_date_dirs;
  for (const auto& entry : std::filesystem::directory_iterator(root, ec)) {
    if (ec) {
      break;
    }
    if (!entry.is_directory(ec)) {
      continue;
    }
    const std::string name = entry.path().filename().string();
    if (is_date_dir_name(name) && name < current_date_dir) {
      old_date_dirs.push_back(entry.path());
    }
  }
  if (ec) {
    if (message != nullptr) {
      *message = "扫描旧图片目录失败: " + root.string() + " error=" + ec.message();
    }
    return false;
  }
  std::sort(old_date_dirs.begin(), old_date_dirs.end());

  std::uintmax_t removed_dirs = 0;
  std::uintmax_t removed_items = 0;
  for (const auto& dir : old_date_dirs) {
    std::error_code remove_ec;
    removed_items += std::filesystem::remove_all(dir, remove_ec);
    if (remove_ec) {
      if (message != nullptr) {
        *message = "删除旧图片目录失败: " + dir.string() + " error=" + remove_ec.message();
      }
      return false;
    }
    ++removed_dirs;
    space = std::filesystem::space(root, ec);
    if (ec) {
      if (message != nullptr) {
        *message = "重新读取图片磁盘容量失败: " + root.string() + " error=" + ec.message();
      }
      return false;
    }
    if (free_ratio(space) >= min_free_ratio) {
      break;
    }
  }

  if (message != nullptr && removed_dirs > 0U) {
    std::ostringstream out;
    out << "图片磁盘可用容量低于 " << static_cast<int>(config.cleanup_min_free_ratio * 100.0F)
        << "%，已清理旧日期目录 " << removed_dirs << " 个，删除条目 " << removed_items
        << " 个，当前可用比例 " << std::fixed << std::setprecision(3) << free_ratio(space);
    *message = out.str();
  } else if (message != nullptr && free_ratio(space) < min_free_ratio) {
    std::ostringstream out;
    out << "图片磁盘可用容量低于 " << static_cast<int>(config.cleanup_min_free_ratio * 100.0F)
        << "%，但没有早于 " << current_date_dir << " 的可清理日期目录";
    *message = out.str();
  }
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
