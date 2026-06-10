#pragma once

#include <cstdint>
#include <string>

#include "common/error_code.hpp"
#include "common/inspection_types.hpp"

namespace seat_aoi {

enum class StationState : std::uint32_t {
  Created = 0,
  Initialized = 1,
  Ready = 2,
  Running = 3,
  Fault = 4,
  Stopped = 5,
};

enum class AlarmLevel : std::uint32_t {
  None = 0,
  Warning = 1,
  Critical = 2,
};

struct StationHealthSnapshot {
  StationState state = StationState::Created;
  AlarmLevel alarm_level = AlarmLevel::None;
  std::uint64_t total_jobs = 0;
  std::uint64_t ok_count = 0;
  std::uint64_t ng_count = 0;
  std::uint64_t recheck_count = 0;
  std::uint64_t error_count = 0;
  std::uint64_t detector_timeout_count = 0;
  std::uint64_t device_fault_count = 0;
  std::uint64_t consecutive_recheck_count = 0;
  std::uint64_t state_changed_at_us = 0;
  std::string alarm_message;
};

class StationHealthMonitor {
public:
  void configure(std::uint32_t warning_recheck_threshold,
                 std::uint32_t critical_recheck_threshold);
  void transition_to(StationState state, const std::string& reason);
  void record_result(InspectionDecision decision,
                     ErrorCode error_code,
                     const std::string& message);
  void record_fault(ErrorCode error_code, const std::string& message);
  StationHealthSnapshot snapshot() const;

private:
  void update_alarm(ErrorCode error_code, const std::string& message);

  StationHealthSnapshot snapshot_{};
  std::uint32_t warning_recheck_threshold_ = 3;
  std::uint32_t critical_recheck_threshold_ = 5;
};

const char* station_state_name(StationState state);
const char* alarm_level_name(AlarmLevel level);

}  // namespace seat_aoi
