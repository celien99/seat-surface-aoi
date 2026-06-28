#include "control/signal_client.hpp"

#include <chrono>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

#include "common/inspection_types.hpp"
#include "common/string_utils.hpp"

namespace seat_aoi {

namespace {

std::vector<std::string> split_csv_line(const std::string& line) {
  std::vector<std::string> fields;
  std::string current;
  std::istringstream stream(line);
  while (std::getline(stream, current, ',')) {
    fields.push_back(trim(current));
  }
  return fields;
}

bool parse_trigger_line(const std::string& line,
                        std::uint64_t fallback_trigger_id,
                        const std::string& default_seat_id,
                        const std::string& default_sku,
                        ExternalTrigger* out_trigger,
                        std::string* error_message) {
  const std::string trimmed = trim(line);
  if (trimmed.empty() || trimmed[0] == '#') {
    if (error_message != nullptr) {
      *error_message = "empty trigger line";
    }
    return false;
  }

  const auto fields = split_csv_line(trimmed);
  ExternalTrigger trigger{};
  trigger.trigger_id = fallback_trigger_id;
  trigger.seat_id = default_seat_id;
  trigger.sku = default_sku;

  if (!fields.empty() && !fields[0].empty()) {
    try {
      trigger.trigger_id = std::stoull(fields[0]);
    } catch (const std::exception&) {
      if (error_message != nullptr) {
        *error_message = "外部触发 trigger_id 非法: " + fields[0];
      }
      return false;
    }
  }
  if (fields.size() > 1 && !fields[1].empty()) {
    trigger.seat_id = fields[1];
  }
  if (fields.size() > 2 && !fields[2].empty()) {
    trigger.sku = fields[2];
  }
  if (trigger.trigger_id == 0 || trigger.seat_id.empty() || trigger.sku.empty()) {
    if (error_message != nullptr) {
      *error_message = "外部触发行必须包含有效 trigger_id、seat_id 和 sku";
    }
    return false;
  }
  *out_trigger = trigger;
  return true;
}

const char* decision_to_external_name(InspectionDecision decision) {
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

}  // namespace

bool SimSignalClient::initialize(const SignalClientConfig& config) {
  initialized_ = true;
  simulate_output_fault_ = config.simulate_output_fault;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  return true;
}

bool SimSignalClient::wait_trigger(ExternalTrigger* out_trigger,
                             int timeout_ms,
                             std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms < 0) {
    if (error_message != nullptr) {
      *error_message = "外部信号客户端未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    const int wait_ms = timeout_ms > 0 ? timeout_ms : 1000;
    std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
    if (error_message != nullptr) {
      *error_message = "模拟外部信号触发超时";
    }
    return false;
  }

  ExternalTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = "SIM_SEAT_" + std::to_string(trigger.trigger_id);
  trigger.sku = "seat_a_black_leather";
  *out_trigger = trigger;
  return true;
}

bool SimSignalClient::publish_result(const ExternalTrigger& /*trigger*/,
                              std::uint64_t /*sequence_id*/,
                              InspectionDecision /*decision*/,
                              int timeout_ms,
                              std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "外部信号客户端未初始化或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_output_fault_) {
    if (error_message != nullptr) {
      *error_message = "模拟外部信号结果发布失败";
    }
    return false;
  }
  return true;
}

SignalHealth SimSignalClient::get_health() const {
  return SignalHealth{initialized_ && !simulate_output_fault_ && !simulate_trigger_timeout_,
                   simulate_output_fault_     ? "模拟外部信号结果发布失败"
                   : simulate_trigger_timeout_ ? "模拟外部信号触发超时"
                                               : "simulated"};
}

bool ManualSignalClient::initialize(const SignalClientConfig& config) {
  initialized_ = true;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  if (!config.station_id.empty()) {
    station_id_ = config.station_id;
  }
  if (!config.default_sku.empty()) {
    sku_ = config.default_sku;
  }
  return true;
}

bool ManualSignalClient::wait_trigger(ExternalTrigger* out_trigger,
                                          int timeout_ms,
                                          std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms < 0) {
    if (error_message != nullptr) {
      *error_message = "手动外部信号未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    const int wait_ms = timeout_ms > 0 ? timeout_ms : 1000;
    std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
    if (error_message != nullptr) {
      *error_message = "手动触发超时";
    }
    return false;
  }

  ExternalTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = station_id_ + "_MANUAL_SEAT_" + std::to_string(trigger.trigger_id);
  trigger.sku = sku_;
  *out_trigger = trigger;
  std::cout << "[trigger_id=" << trigger.trigger_id
            << "] manual trigger accepted seat_id=" << trigger.seat_id
            << " sku=" << trigger.sku << std::endl;
  return true;
}

bool ManualSignalClient::publish_result(const ExternalTrigger& trigger,
                                           std::uint64_t sequence_id,
                                           InspectionDecision decision,
                                           int timeout_ms,
                                           std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "手动外部信号未初始化或 timeout_ms 非法";
    }
    return false;
  }
  std::cout << "[sequence_id=" << sequence_id << " trigger_id=" << trigger.trigger_id
            << "] manual trigger decision recorded decision="
            << static_cast<std::uint32_t>(decision) << std::endl;
  return true;
}

SignalHealth ManualSignalClient::get_health() const {
  return SignalHealth{initialized_ && !simulate_trigger_timeout_,
                   simulate_trigger_timeout_ ? "手动触发超时" : "manual_trigger"};
}

bool ExternalSignalClient::initialize(const SignalClientConfig& config) {
  initialized_ = true;
  simulate_output_fault_ = config.simulate_output_fault;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  if (!config.station_id.empty()) {
    station_id_ = config.station_id;
  }
  if (!config.default_seat_id.empty()) {
    default_seat_id_ = config.default_seat_id;
  }
  if (!config.default_sku.empty()) {
    default_sku_ = config.default_sku;
  }
  trigger_queue_path_ = config.trigger_queue_path;
  result_queue_path_ = config.result_queue_path;
  return true;
}

bool ExternalSignalClient::wait_trigger(ExternalTrigger* out_trigger,
                                        int timeout_ms,
                                        std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr) {
    if (error_message != nullptr) {
      *error_message = "外部信号客户端未初始化或输出指针为空";
    }
    return false;
  }
  if (timeout_ms < 0) {
    if (error_message != nullptr) {
      *error_message = "外部信号客户端 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    const int wait_ms = timeout_ms > 0 ? timeout_ms : 1000;
    std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
    if (error_message != nullptr) {
      *error_message = "外部信号触发超时";
    }
    return false;
  }

  if (trigger_queue_path_.empty()) {
    ExternalTrigger trigger{};
    trigger.trigger_id = next_trigger_id_++;
    trigger.seat_id = station_id_ + "_" + default_seat_id_ + "_" +
                      std::to_string(trigger.trigger_id);
    trigger.sku = default_sku_;
    *out_trigger = trigger;
    return true;
  }

  // timeout_ms <= 0 → 无限等待，每 5ms 检查一次文件
  const bool infinite = (timeout_ms <= 0);
  const auto deadline = infinite
      ? std::chrono::steady_clock::time_point::max()
      : std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  std::string parse_error;
  while (std::chrono::steady_clock::now() < deadline) {
    std::ifstream input(trigger_queue_path_);
    if (input.good()) {
      std::string line;
      std::size_t line_index = 0;
      while (std::getline(input, line)) {
        if (line_index++ < consumed_lines_) {
          continue;
        }
        ++consumed_lines_;
        ExternalTrigger trigger{};
        if (parse_trigger_line(line,
                               next_trigger_id_,
                               default_seat_id_,
                               default_sku_,
                               &trigger,
                               &parse_error)) {
          next_trigger_id_ = trigger.trigger_id + 1;
          *out_trigger = trigger;
          return true;
        }
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }

  if (error_message != nullptr) {
    *error_message = parse_error.empty() ? "" : "外部信号触发超时: " + parse_error;
  }
  return false;
}

bool ExternalSignalClient::publish_result(const ExternalTrigger& trigger,
                                          std::uint64_t sequence_id,
                                          InspectionDecision decision,
                                          int timeout_ms,
                                          std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "外部信号客户端未初始化或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_output_fault_) {
    if (error_message != nullptr) {
      *error_message = "外部信号结果发布失败";
    }
    return false;
  }

  const char* decision_name = decision_to_external_name(decision);
  if (result_queue_path_.empty()) {
    std::cout << "[sequence_id=" << sequence_id << " trigger_id=" << trigger.trigger_id
              << "] external signal result decision=" << decision_name << std::endl;
    return true;
  }

  std::ofstream output(result_queue_path_, std::ios::app);
  if (!output.good()) {
    if (error_message != nullptr) {
      *error_message = "无法打开外部信号结果队列文件: " + result_queue_path_;
    }
    return false;
  }
  output << sequence_id << ',' << trigger.trigger_id << ',' << trigger.seat_id << ','
         << trigger.sku << ',' << decision_name << '\n';
  return true;
}

SignalHealth ExternalSignalClient::get_health() const {
  return SignalHealth{
      initialized_ && !simulate_output_fault_ && !simulate_trigger_timeout_,
      simulate_output_fault_     ? "外部信号结果发布失败"
      : simulate_trigger_timeout_ ? "外部信号触发超时"
                                  : "external_signal"};
}

}  // namespace seat_aoi
