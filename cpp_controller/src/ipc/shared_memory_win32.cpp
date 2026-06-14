#include "ipc/shared_memory.hpp"

#ifdef _WIN32

#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <algorithm>
#include <string>
#include <utility>

namespace seat_aoi {

namespace {

std::wstring widen_ascii(const std::string& value) {
  return std::wstring(value.begin(), value.end());
}

std::string windows_mapping_name(const std::string& name) {
  std::string mapped = name;
  mapped.erase(std::remove(mapped.begin(), mapped.end(), '/'), mapped.end());
  if (mapped.rfind("Local\\", 0) == 0 || mapped.rfind("Global\\", 0) == 0) {
    return mapped;
  }
  return "Local\\" + mapped;
}

}  // namespace

SharedMemory::SharedMemory(SharedMemory&& other) noexcept {
  *this = std::move(other);
}

SharedMemory& SharedMemory::operator=(SharedMemory&& other) noexcept {
  if (this == &other) {
    return *this;
  }
  close();
  mapping_handle_ = other.mapping_handle_;
  data_ = other.data_;
  size_ = other.size_;
  name_ = std::move(other.name_);
  was_created_ = other.was_created_;
  other.mapping_handle_ = nullptr;
  other.data_ = nullptr;
  other.size_ = 0;
  other.was_created_ = false;
  return *this;
}

SharedMemory::~SharedMemory() {
  close();
}

bool SharedMemory::create_or_open(const std::string& name, std::size_t size, bool reset) {
  close();
  was_created_ = false;

  const std::string mapped_name = windows_mapping_name(name);
  const std::wstring wide_name = widen_ascii(mapped_name);

  if (reset) {
    HANDLE existing = ::OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, wide_name.c_str());
    if (existing != nullptr) {
      ::CloseHandle(existing);
      return false;
    }
  }

  HANDLE handle = nullptr;
  bool created = false;
  if (!reset) {
    handle = ::OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, wide_name.c_str());
  }
  if (handle == nullptr) {
    const DWORD size_high = static_cast<DWORD>((static_cast<unsigned long long>(size) >> 32U) & 0xFFFFFFFFULL);
    const DWORD size_low = static_cast<DWORD>(static_cast<unsigned long long>(size) & 0xFFFFFFFFULL);
    handle = ::CreateFileMappingW(INVALID_HANDLE_VALUE,
                                  nullptr,
                                  PAGE_READWRITE,
                                  size_high,
                                  size_low,
                                  wide_name.c_str());
    if (handle == nullptr) {
      return false;
    }
    created = ::GetLastError() != ERROR_ALREADY_EXISTS;
    if (!created) {
      ::CloseHandle(handle);
      return false;
    }
  }

  void* mapped = ::MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, size);
  if (mapped == nullptr) {
    ::CloseHandle(handle);
    return false;
  }

  mapping_handle_ = handle;
  data_ = mapped;
  size_ = size;
  name_ = mapped_name;
  was_created_ = created;
  return true;
}

bool SharedMemory::open_existing(const std::string& name, std::size_t size) {
  close();
  was_created_ = false;

  const std::string mapped_name = windows_mapping_name(name);
  const std::wstring wide_name = widen_ascii(mapped_name);
  HANDLE handle = ::OpenFileMappingW(FILE_MAP_ALL_ACCESS, FALSE, wide_name.c_str());
  if (handle == nullptr) {
    return false;
  }
  void* mapped = ::MapViewOfFile(handle, FILE_MAP_ALL_ACCESS, 0, 0, size);
  if (mapped == nullptr) {
    ::CloseHandle(handle);
    return false;
  }

  mapping_handle_ = handle;
  data_ = mapped;
  size_ = size;
  name_ = mapped_name;
  return true;
}

void SharedMemory::close() {
  if (data_ != nullptr) {
    ::UnmapViewOfFile(data_);
    data_ = nullptr;
  }
  if (mapping_handle_ != nullptr) {
    ::CloseHandle(static_cast<HANDLE>(mapping_handle_));
    mapping_handle_ = nullptr;
  }
  size_ = 0;
  was_created_ = false;
}

void SharedMemory::unlink_name() {
  // Windows named mappings disappear after the last handle is closed. If another
  // process still owns the mapping, startup with reset=true fails fast instead
  // of silently reusing stale shared memory.
  close();
}

}  // namespace seat_aoi

#endif
