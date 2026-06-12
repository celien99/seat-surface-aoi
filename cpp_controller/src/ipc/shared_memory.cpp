#include "ipc/shared_memory.hpp"

#include <cerrno>
#include <cstring>
#include <stdexcept>
#include <utility>

#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <unistd.h>

namespace seat_aoi {

SharedMemory::SharedMemory(SharedMemory&& other) noexcept {
  *this = std::move(other);
}

SharedMemory& SharedMemory::operator=(SharedMemory&& other) noexcept {
  if (this == &other) {
    return *this;
  }
  close();
  fd_ = other.fd_;
  data_ = other.data_;
  size_ = other.size_;
  name_ = std::move(other.name_);
  was_created_ = other.was_created_;
  other.fd_ = -1;
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
  if (reset) {
    ::shm_unlink(name.c_str());
  }

  bool created = reset;
  fd_ = ::shm_open(name.c_str(), reset ? (O_CREAT | O_RDWR) : O_RDWR, 0600);
  if (fd_ < 0 && !reset && errno == ENOENT) {
    fd_ = ::shm_open(name.c_str(), O_CREAT | O_RDWR, 0600);
    created = true;
  }
  if (fd_ < 0) {
    return false;
  }
  if (created && ::ftruncate(fd_, static_cast<off_t>(size)) != 0) {
    close();
    return false;
  }

  data_ = ::mmap(nullptr, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd_, 0);
  if (data_ == MAP_FAILED) {
    data_ = nullptr;
    close();
    return false;
  }

  size_ = size;
  name_ = name;
  was_created_ = created;
  return true;
}

void SharedMemory::close() {
  if (data_ != nullptr) {
    ::munmap(data_, size_);
    data_ = nullptr;
  }
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
  size_ = 0;
  was_created_ = false;
}

void SharedMemory::unlink_name() {
  if (!name_.empty()) {
    ::shm_unlink(name_.c_str());
  }
}

}  // namespace seat_aoi
