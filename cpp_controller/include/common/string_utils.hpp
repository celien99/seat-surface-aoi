#pragma once

#include <cstddef>
#include <cstring>
#include <string>

namespace seat_aoi {

inline std::string trim(const std::string& value) {
  const auto begin = value.find_first_not_of(" \t\r\n");
  if (begin == std::string::npos) {
    return "";
  }
  const auto end = value.find_last_not_of(" \t\r\n");
  return value.substr(begin, end - begin + 1);
}

template <std::size_t N>
inline void copy_cstr(char (&dst)[N], const std::string& value) {
  std::memset(dst, 0, N);
  std::strncpy(dst, value.c_str(), N - 1);
}

inline std::string fixed_cstr_to_string(const char* value, std::size_t size) {
  const auto* end = static_cast<const char*>(std::memchr(value, '\0', size));
  if (end == nullptr) {
    return std::string(value, size);
  }
  return std::string(value, static_cast<std::size_t>(end - value));
}

}  // namespace seat_aoi

