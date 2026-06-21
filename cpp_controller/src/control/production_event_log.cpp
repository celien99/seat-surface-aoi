#include "control/production_event_log.hpp"

#include <ctime>
#include <filesystem>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <vector>

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
  trace_root_ = trace_root;
  rotate_if_needed();
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

void ProductionEventLog::rotate_if_needed() {
  const std::string path = trace_root_ + "/cpp_controller_events.jsonl";
  std::error_code ec;
  if (!std::filesystem::is_regular_file(path, ec)) {
    return;
  }
  const auto size = std::filesystem::file_size(path, ec);
  if (ec || size < kMaxEventLogBytes) {
    return;
  }

  // 关闭当前输出流
  output_.close();

  // 生成带日期的轮转文件名
  const std::time_t now = std::time(nullptr);
  std::tm local_time{};
#ifdef _WIN32
  localtime_s(&local_time, &now);
#else
  localtime_r(&now, &local_time);
#endif
  std::ostringstream date_suffix;
  date_suffix << std::put_time(&local_time, "%Y%m%d");
  const std::string rotated_path =
      trace_root_ + "/cpp_controller_events." + date_suffix.str() + ".jsonl";

  std::filesystem::rename(path, rotated_path, ec);
  if (ec) {
    std::cerr << "production_event_log rotate rename failed: " << ec.message() << std::endl;
    return;
  }

  // 保留最近 kMaxRotatedLogs 个轮转文件，删除更旧的
  std::vector<std::filesystem::path> rotated_files;
  for (const auto& entry : std::filesystem::directory_iterator(trace_root_, ec)) {
    if (ec) break;
    const auto& name = entry.path().filename().string();
    if (name.find("cpp_controller_events.") == 0 && name != "cpp_controller_events.jsonl") {
      rotated_files.push_back(entry.path());
    }
  }
  if (rotated_files.size() > static_cast<std::size_t>(kMaxRotatedLogs)) {
    std::sort(rotated_files.begin(), rotated_files.end());
    for (std::size_t i = 0; i < rotated_files.size() - kMaxRotatedLogs; ++i) {
      std::filesystem::remove(rotated_files[i], ec);
    }
  }
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
