#pragma once

#include <string>

namespace seat_aoi {

struct JkLrdPresenceGateConfig {
  bool enabled = false;
  std::string dll_path = "jklrd_driver.dll";
  std::string port;
  int baud_rate = 9600;
  int slave_addr = 1;
  int lower_mm = 0;
  int upper_mm = 0;
  int stable_ms = 300;
  int poll_interval_ms = 100;
  int timeout_ms = 5000;
};

class JkLrdPresenceGate {
public:
  ~JkLrdPresenceGate();

  bool initialize(const JkLrdPresenceGateConfig& config, std::string* error_message);
  bool wait_until_present(int* out_distance_mm, std::string* error_message);
  void close();

  bool enabled() const { return config_.enabled; }

private:
  std::string last_error() const;

  JkLrdPresenceGateConfig config_;

#ifdef _WIN32
  using OpenFn = int(__cdecl*)(const char*, int, int);
  using CloseFn = int(__cdecl*)();
  using ReadDistanceFn = int(__cdecl*)();
  using GetLastErrorFn = const char*(__cdecl*)();

  void* dll_ = nullptr;
  OpenFn open_ = nullptr;
  CloseFn close_ = nullptr;
  ReadDistanceFn read_distance_ = nullptr;
  GetLastErrorFn get_last_error_ = nullptr;
#endif
};

}  // namespace seat_aoi
