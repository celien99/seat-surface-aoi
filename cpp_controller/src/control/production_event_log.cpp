#include "control/production_event_log.hpp"

#include <filesystem>
#include <sstream>

#include "common/time_utils.hpp"

namespace seat_aoi {

namespace {

bool ensure_directory(const std::string& path, std::string* error_message) {
  if (path.empty()) {
    if (error_message != nullptr) {
      *error_message = "trace_root 不能为空";
    }
    return false;
  }
  std::error_code ec;
  if (std::filesystem::is_directory(path, ec)) {
    return true;
  }
  if (!std::filesystem::create_directories(path, ec) && !std::filesystem::is_directory(path, ec)) {
    if (error_message != nullptr) {
      *error_message = "创建 trace 目录失败: " + path + " error=" + ec.message();
    }
    return false;
  }
  return true;
}

std::string json_escape(const std::string& value) {
  std::ostringstream escaped;
  for (const char ch : value) {
    switch (ch) {
      case '\\':
        escaped << "\\\\";
        break;
      case '"':
        escaped << "\\\"";
        break;
      case '\n':
        escaped << "\\n";
        break;
      case '\r':
        escaped << "\\r";
        break;
      case '\t':
        escaped << "\\t";
        break;
      default:
        escaped << ch;
        break;
    }
  }
  return escaped.str();
}

}  // namespace

const char* inspection_decision_name(InspectionDecision decision) {
  switch (decision) {
    case InspectionDecision::OK:
      return "OK";
    case InspectionDecision::NG:
      return "NG";
    case InspectionDecision::Recheck:
      return "RECHECK";
    case InspectionDecision::Error:
      return "ERROR";
  }
  return "UNKNOWN";
}

const char* error_code_name(ErrorCode error_code) {
  switch (error_code) {
    case ErrorCode::None:
      return "None";
    case ErrorCode::ProtocolMismatch:
      return "ProtocolMismatch";
    case ErrorCode::InvalidPayload:
      return "InvalidPayload";
    case ErrorCode::CrcMismatch:
      return "CrcMismatch";
    case ErrorCode::SlotUnavailable:
      return "SlotUnavailable";
    case ErrorCode::DetectorTimeout:
      return "DetectorTimeout";
    case ErrorCode::MissingFrame:
      return "MissingFrame";
    case ErrorCode::QualityFailed:
      return "QualityFailed";
    case ErrorCode::DeviceFault:
      return "DeviceFault";
    case ErrorCode::InternalError:
      return "InternalError";
    case ErrorCode::LightFault:
      return "LightFault";
    case ErrorCode::CameraFault:
      return "CameraFault";
    case ErrorCode::TriggerSyncFault:
      return "TriggerSyncFault";
    case ErrorCode::ConfigurationError:
      return "ConfigurationError";
    case ErrorCode::Reserved14:
      return "Reserved14";
  }
  return "UnknownError";
}

bool ProductionEventLog::initialize(const std::string& trace_root,
                                    std::string* error_message) {
  std::lock_guard<std::mutex> lock(mutex_);
  enabled_ = false;
  output_.close();
  if (!ensure_directory(trace_root, error_message)) {
    return false;
  }
  const std::string path = trace_root + "/cpp_controller_events.jsonl";
  output_.open(path, std::ios::out | std::ios::app);
  if (!output_.is_open()) {
    if (error_message != nullptr) {
      *error_message = "打开 C++ 生产事件日志失败: " + path;
    }
    return false;
  }
  enabled_ = true;
  return true;
}

void ProductionEventLog::record(const ProductionEvent& event) {
  std::lock_guard<std::mutex> lock(mutex_);
  if (!enabled_ || !output_.is_open()) {
    return;
  }
  output_ << "{"
          << "\"timestamp_us\":" << now_us() << ","
          << "\"event\":\"" << json_escape(event.name) << "\","
          << "\"sequence_id\":" << event.sequence_id << ","
          << "\"trigger_id\":" << event.trigger_id << ","
          << "\"seat_id\":\"" << json_escape(event.seat_id) << "\","
          << "\"sku\":\"" << json_escape(event.sku) << "\","
          << "\"decision\":\"" << inspection_decision_name(event.decision) << "\","
          << "\"decision_code\":" << static_cast<std::uint32_t>(event.decision) << ","
          << "\"error\":\"" << error_code_name(event.error_code) << "\","
          << "\"error_code\":" << static_cast<std::uint32_t>(event.error_code) << ","
          << "\"station_state\":\"" << station_state_name(event.health.state) << "\","
          << "\"alarm_level\":\"" << alarm_level_name(event.health.alarm_level) << "\","
          << "\"total_jobs\":" << event.health.total_jobs << ","
          << "\"ok_count\":" << event.health.ok_count << ","
          << "\"ng_count\":" << event.health.ng_count << ","
          << "\"recheck_count\":" << event.health.recheck_count << ","
          << "\"error_count\":" << event.health.error_count << ","
          << "\"detector_timeout_count\":" << event.health.detector_timeout_count << ","
          << "\"device_fault_count\":" << event.health.device_fault_count << ","
          << "\"consecutive_recheck_count\":" << event.health.consecutive_recheck_count << ","
          << "\"health_message\":\"" << json_escape(event.health.alarm_message) << "\","
          << "\"message\":\"" << json_escape(event.message) << "\""
          << "}\n";
  output_.flush();
}

}  // namespace seat_aoi
