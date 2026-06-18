#pragma once

#include <cstdint>
#include <string>

namespace seat_aoi {

struct DistanceSensorConfig {
  std::string serial_port;
  std::uint32_t baud_rate = 9600;
  std::uint32_t slave_address = 1;
  std::uint32_t threshold_mm = 500;
  std::uint32_t trigger_delay_ms = 500;
  std::uint32_t poll_interval_ms = 50;
};

class DistanceSensor {
public:
  DistanceSensor() = default;
  ~DistanceSensor();

  bool initialize(const DistanceSensorConfig& config, std::string* error_message);
  void shutdown();

  /// 读取当前距离值（毫米），失败返回 -1
  int read_distance_mm(std::string* error_message);

  /// 执行一次触发轮询：读取距离 → 运行状态机。
  /// @return true 表示触发就绪（已完成消抖并触发）
  bool poll_trigger();

  /// 重置状态机到 ARMED
  void reset_trigger();

private:
  void close_serial();
  static std::uint16_t crc16(const std::uint8_t* data, std::size_t length);

  bool initialized_ = false;
  DistanceSensorConfig config_{};

#ifdef _WIN32
  void* handle_ = nullptr;
#else
  int fd_ = -1;
#endif

  enum class State { Armed, Debouncing, Triggered } state_ = State::Armed;
  std::uint64_t debounce_start_ms_ = 0;
  std::uint64_t cooldown_start_ms_ = 0;
};

}  // namespace seat_aoi
