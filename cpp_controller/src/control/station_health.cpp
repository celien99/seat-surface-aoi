#include "control/station_health.hpp"

#include "common/time_utils.hpp"

namespace seat_aoi {

namespace {

bool is_device_fault(ErrorCode error_code) {
  return error_code == ErrorCode::DeviceFault ||
         error_code == ErrorCode::LightFault ||
         error_code == ErrorCode::CameraFault ||
         error_code == ErrorCode::TriggerSyncFault ||
         error_code == ErrorCode::RobotFault;
}

}  // namespace

void StationHealthMonitor::configure(std::uint32_t warning_recheck_threshold,
                                     std::uint32_t critical_recheck_threshold) {
  warning_recheck_threshold_ = warning_recheck_threshold == 0 ? 1 : warning_recheck_threshold;
  critical_recheck_threshold_ =
      critical_recheck_threshold <= warning_recheck_threshold_
          ? warning_recheck_threshold_ + 1
          : critical_recheck_threshold;
}

void StationHealthMonitor::transition_to(StationState state, const std::string& reason) {
  if (snapshot_.state == state && snapshot_.alarm_message == reason) {
    return;
  }
  snapshot_.state = state;
  snapshot_.state_changed_at_us = now_us();
  if (!reason.empty()) {
    snapshot_.alarm_message = reason;
  }
  if (state == StationState::Fault) {
    snapshot_.alarm_level = AlarmLevel::Critical;
  } else if (state == StationState::Ready || state == StationState::Running) {
    if (snapshot_.consecutive_recheck_count == 0) {
      snapshot_.alarm_level = AlarmLevel::None;
      snapshot_.alarm_message.clear();
    }
  }
}

void StationHealthMonitor::record_result(InspectionDecision decision,
                                         ErrorCode error_code,
                                         const std::string& message) {
  ++snapshot_.total_jobs;
  switch (decision) {
    case InspectionDecision::OK:
      ++snapshot_.ok_count;
      snapshot_.consecutive_recheck_count = 0;
      if (snapshot_.state != StationState::Fault) {
        snapshot_.alarm_level = AlarmLevel::None;
        snapshot_.alarm_message.clear();
      }
      break;
    case InspectionDecision::NG:
      ++snapshot_.ng_count;
      snapshot_.consecutive_recheck_count = 0;
      break;
    case InspectionDecision::Recheck:
      ++snapshot_.recheck_count;
      ++snapshot_.consecutive_recheck_count;
      update_alarm(error_code, message);
      break;
    case InspectionDecision::Error:
      ++snapshot_.error_count;
      ++snapshot_.consecutive_recheck_count;
      update_alarm(error_code, message);
      break;
  }
  if (error_code == ErrorCode::DetectorTimeout) {
    ++snapshot_.detector_timeout_count;
  }
  if (is_device_fault(error_code)) {
    ++snapshot_.device_fault_count;
  }
}

void StationHealthMonitor::record_fault(ErrorCode error_code, const std::string& message) {
  if (error_code == ErrorCode::DetectorTimeout) {
    ++snapshot_.detector_timeout_count;
  }
  if (is_device_fault(error_code)) {
    ++snapshot_.device_fault_count;
  }
  update_alarm(error_code, message);
}

StationHealthSnapshot StationHealthMonitor::snapshot() const {
  return snapshot_;
}

void StationHealthMonitor::update_alarm(ErrorCode error_code, const std::string& message) {
  if (snapshot_.consecutive_recheck_count >= critical_recheck_threshold_ ||
      error_code == ErrorCode::DeviceFault ||
      error_code == ErrorCode::DetectorTimeout) {
    snapshot_.alarm_level = AlarmLevel::Critical;
    snapshot_.state = StationState::Fault;
  } else if (snapshot_.consecutive_recheck_count >= warning_recheck_threshold_ ||
             error_code != ErrorCode::None) {
    snapshot_.alarm_level = AlarmLevel::Warning;
  }
  if (!message.empty()) {
    snapshot_.alarm_message = message;
  }
}

const char* station_state_name(StationState state) {
  switch (state) {
    case StationState::Created:
      return "Created";
    case StationState::Initialized:
      return "Initialized";
    case StationState::Ready:
      return "Ready";
    case StationState::Running:
      return "Running";
    case StationState::Fault:
      return "Fault";
    case StationState::Stopped:
      return "Stopped";
  }
  return "Unknown";
}

const char* alarm_level_name(AlarmLevel level) {
  switch (level) {
    case AlarmLevel::None:
      return "None";
    case AlarmLevel::Warning:
      return "Warning";
    case AlarmLevel::Critical:
      return "Critical";
  }
  return "Unknown";
}

}  // namespace seat_aoi
