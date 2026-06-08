#include "ipc/crc32.hpp"

namespace seat_aoi {

std::uint32_t crc32(const void* data, std::size_t size) {
  static std::uint32_t table[256] = {};
  static bool initialized = false;
  if (!initialized) {
    for (std::uint32_t i = 0; i < 256; ++i) {
      std::uint32_t c = i;
      for (int j = 0; j < 8; ++j) {
        c = (c & 1U) ? (0xEDB88320U ^ (c >> 1U)) : (c >> 1U);
      }
      table[i] = c;
    }
    initialized = true;
  }

  const auto* bytes = static_cast<const std::uint8_t*>(data);
  std::uint32_t c = 0xFFFFFFFFU;
  for (std::size_t i = 0; i < size; ++i) {
    c = table[(c ^ bytes[i]) & 0xFFU] ^ (c >> 8U);
  }
  return c ^ 0xFFFFFFFFU;
}

}  // namespace seat_aoi

