#pragma once

#include <chrono>
#include <cstdint>

namespace seat_aoi {

inline std::uint64_t now_us() {
  const auto now = std::chrono::system_clock::now().time_since_epoch();
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::microseconds>(now).count());
}

}  // namespace seat_aoi

