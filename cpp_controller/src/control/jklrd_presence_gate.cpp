#include "control/jklrd_presence_gate.hpp"

#include <algorithm>
#include <chrono>
#include <thread>

#ifdef _WIN32
#include <windows.h>
#endif

namespace seat_aoi {

JkLrdPresenceGate::~JkLrdPresenceGate() {
  close();
}

bool JkLrdPresenceGate::initialize(const JkLrdPresenceGateConfig& config,
                                   std::string* error_message) {
  config_ = config;
  if (!config_.enabled) {
    return true;
  }

#ifndef _WIN32
  if (error_message != nullptr) {
    *error_message = "JK-LRD 位移传感器 DLL 只支持 Windows 工控机";
  }
  return false;
#else
  dll_ = reinterpret_cast<void*>(::LoadLibraryA(config_.dll_path.c_str()));
  if (dll_ == nullptr) {
    if (error_message != nullptr) {
      *error_message = "无法加载 JK-LRD DLL: " + config_.dll_path;
    }
    return false;
  }

  auto* module = static_cast<HMODULE>(dll_);
  open_ = reinterpret_cast<OpenFn>(::GetProcAddress(module, "jklrd_open"));
  close_ = reinterpret_cast<CloseFn>(::GetProcAddress(module, "jklrd_close"));
  read_distance_ =
      reinterpret_cast<ReadDistanceFn>(::GetProcAddress(module, "jklrd_read_distance"));
  get_last_error_ =
      reinterpret_cast<GetLastErrorFn>(::GetProcAddress(module, "jklrd_get_last_error"));
  if (open_ == nullptr || close_ == nullptr || read_distance_ == nullptr ||
      get_last_error_ == nullptr) {
    if (error_message != nullptr) {
      *error_message = "JK-LRD DLL 缺少必需导出函数";
    }
    close();
    return false;
  }

  if (open_(config_.port.c_str(), config_.baud_rate, config_.slave_addr) != 0) {
    if (error_message != nullptr) {
      *error_message = "打开 JK-LRD 传感器失败: " + last_error();
    }
    close();
    return false;
  }
  return true;
#endif
}

bool JkLrdPresenceGate::wait_until_present(int* out_distance_mm,
                                           std::string* error_message) {
  if (!config_.enabled) {
    return true;
  }

#ifndef _WIN32
  if (error_message != nullptr) {
    *error_message = "JK-LRD 位移传感器 DLL 只支持 Windows 工控机";
  }
  return false;
#else
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(config_.timeout_ms);
  const int low = std::min(config_.lower_mm, config_.upper_mm);
  const int high = std::max(config_.lower_mm, config_.upper_mm);
  const int stable_ms = std::max(config_.stable_ms, 0);
  const auto poll = std::chrono::milliseconds(std::max(config_.poll_interval_ms, 10));
  auto in_range_since = std::chrono::steady_clock::time_point{};

  while (std::chrono::steady_clock::now() < deadline) {
    const int distance = read_distance_();
    const auto now = std::chrono::steady_clock::now();
    const bool in_range = distance >= low && distance <= high;
    if (!in_range) {
      in_range_since = std::chrono::steady_clock::time_point{};
      std::this_thread::sleep_for(poll);
      continue;
    }

    if (in_range_since == std::chrono::steady_clock::time_point{}) {
      in_range_since = now;
    }
    const auto stable_for = std::chrono::duration_cast<std::chrono::milliseconds>(
        now - in_range_since);
    if (stable_for.count() >= stable_ms) {
      if (out_distance_mm != nullptr) {
        *out_distance_mm = distance;
      }
      return true;
    }
    std::this_thread::sleep_for(poll);
  }

  if (error_message != nullptr) {
    *error_message = "JK-LRD 到位等待超时";
  }
  return false;
#endif
}

void JkLrdPresenceGate::close() {
#ifdef _WIN32
  if (close_ != nullptr) {
    close_();
  }
  if (dll_ != nullptr) {
    ::FreeLibrary(static_cast<HMODULE>(dll_));
  }
  dll_ = nullptr;
  open_ = nullptr;
  close_ = nullptr;
  read_distance_ = nullptr;
  get_last_error_ = nullptr;
#endif
}

std::string JkLrdPresenceGate::last_error() const {
#ifdef _WIN32
  if (get_last_error_ == nullptr) {
    return "unknown";
  }
  const char* message = get_last_error_();
  return message == nullptr ? "unknown" : std::string(message);
#else
  return "unsupported platform";
#endif
}

}  // namespace seat_aoi
