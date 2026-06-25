#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

struct PngImage {
  std::uint32_t width = 0;
  std::uint32_t height = 0;
  std::uint32_t channels = 0;
  std::vector<std::uint8_t> pixels;
};

bool read_png_image(const std::string& path, PngImage* out_image, std::string* error);

}  // namespace seat_aoi
