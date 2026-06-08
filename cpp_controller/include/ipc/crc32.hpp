#pragma once

#include <cstddef>
#include <cstdint>

namespace seat_aoi {

std::uint32_t crc32(const void* data, std::size_t size);

}  // namespace seat_aoi

