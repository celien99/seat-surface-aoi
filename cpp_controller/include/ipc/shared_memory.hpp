#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace seat_aoi {

class SharedMemory {
public:
  SharedMemory() = default;
  SharedMemory(const SharedMemory&) = delete;
  SharedMemory& operator=(const SharedMemory&) = delete;
  SharedMemory(SharedMemory&& other) noexcept;
  SharedMemory& operator=(SharedMemory&& other) noexcept;
  ~SharedMemory();

  bool create_or_open(const std::string& name, std::size_t size, bool reset);
  void close();
  void unlink_name();

  void* data() { return data_; }
  const void* data() const { return data_; }
  std::size_t size() const { return size_; }
  const std::string& name() const { return name_; }
  bool is_open() const { return data_ != nullptr; }
  bool was_created() const { return was_created_; }

private:
  int fd_ = -1;
  void* data_ = nullptr;
  std::size_t size_ = 0;
  std::string name_;
  bool was_created_ = false;
};

}  // namespace seat_aoi
