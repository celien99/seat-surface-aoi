#include "control/image_writer.hpp"

#include <fstream>
#include <iostream>
#include <sstream>

#ifdef _WIN32
#include <direct.h>
#define mkdir_impl(path) _mkdir(path)
#else
#include <sys/stat.h>
#define mkdir_impl(path) mkdir(path, 0755)
#endif

namespace seat_aoi {

bool make_dirs(const std::string& path, std::string* error) {
  if (path.empty()) return true;

  std::string current;
  for (std::size_t i = 0; i < path.size(); ++i) {
    current.push_back(path[i]);
    if (path[i] == '/' || path[i] == '\\' || i + 1 == path.size()) {
      if (!current.empty() && current.back() != '/' && current.back() != '\\') {
        if (current.size() > 1 && current.back() != ':') {
          if (mkdir_impl(current.c_str()) != 0 && errno != EEXIST) {
            if (error != nullptr) {
              std::ostringstream oss;
              oss << "mkdir failed: " << current << " (errno=" << errno << ")";
              *error = oss.str();
            }
            return false;
          }
        }
      }
    }
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
