#include "control/distance_trigger_signal_client.hpp"

#include <chrono>
#include <ctime>
#include <iomanip>
#include <iostream>
#include <sstream>

namespace seat_aoi {

DistanceTriggerSignalClient::DistanceTriggerSignalClient(
    std::unique_ptr<ISignalClient> delegate)
    : delegate_(std::move(delegate)) {}

DistanceTriggerSignalClient::~DistanceTriggerSignalClient() {
  sensor_.shutdown();
}

bool DistanceTriggerSignalClient::initialize(const SignalClientConfig& config) {
  if (!delegate_) return false;

  station_id_ = config.station_id.empty() ? "DIST_AOI" : config.station_id;
  default_sku_ = config.default_sku.empty() ? "seat_a_black_leather" : config.default_sku;
  next_trigger_id_ = 1;
  last_sn_.clear();

  sensor_enabled_ = !config.trigger_queue_path.empty();
  if (sensor_enabled_) {
    // trigger_queue_path 作为 distance sensor 配置字段组合: port,baud,addr,threshold,delay,poll
    // 格式: "COM4,9600,1,500,500,50"
    std::string cfg = config.trigger_queue_path;
    sensor_config_.serial_port = cfg;
    // 简化：直接用串口名，其余用默认值
    sensor_config_.baud_rate = 9600;
    sensor_config_.slave_address = 1;
    sensor_config_.threshold_mm = 500;
    sensor_config_.trigger_delay_ms = 500;
    sensor_config_.poll_interval_ms = 50;

    // 尝试解析逗号分隔的完整配置
    std::istringstream ss(cfg);
    std::string field;
    int idx = 0;
    while (std::getline(ss, field, ',') && idx < 6) {
      try {
        switch (idx) {
          case 0: sensor_config_.serial_port = field; break;
          case 1: sensor_config_.baud_rate = static_cast<std::uint32_t>(std::stoul(field)); break;
          case 2: sensor_config_.slave_address = static_cast<std::uint32_t>(std::stoul(field)); break;
          case 3: sensor_config_.threshold_mm = static_cast<std::uint32_t>(std::stoul(field)); break;
          case 4: sensor_config_.trigger_delay_ms = static_cast<std::uint32_t>(std::stoul(field)); break;
          case 5: sensor_config_.poll_interval_ms = static_cast<std::uint32_t>(std::stoul(field)); break;
        }
      } catch (...) {}
      ++idx;
    }
  }

  if (!delegate_->initialize(config)) {
    return false;
  }

  if (sensor_enabled_) {
    std::string sensor_error;
    if (!sensor_.initialize(sensor_config_, &sensor_error)) {
      std::cerr << "距离传感器初始化失败: " << sensor_error << std::endl;
      // 非致命：继续使用上游 delegate
    } else {
      std::cout << "距离传感器已连接 " << sensor_config_.serial_port
                << " threshold=" << sensor_config_.threshold_mm << "mm" << std::endl;
    }
  }

  initialized_ = true;
  return true;
}

bool DistanceTriggerSignalClient::wait_trigger(ExternalTrigger* out_trigger,
                                                int timeout_ms,
                                                std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms <= 0) {
    if (error_message) *error_message = "距离信号客户端未初始化或参数非法";
    return false;
  }

  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);

  // 从上游 delegate 获取 SN（非阻塞轮询）
  std::string sn_error;
  ExternalTrigger sn_trigger{};
  if (delegate_->wait_trigger(&sn_trigger, 100, &sn_error)) {
    last_sn_ = sn_trigger.seat_id;
    std::cout << "距离信号: SN 已缓存 seat_id=" << last_sn_ << std::endl;
  }

  // 轮询距离传感器触发
  while (std::chrono::steady_clock::now() < deadline) {
    if (sensor_enabled_ && sensor_.poll_trigger()) {
      if (last_sn_.empty()) {
        // 自动生成 SN
        const auto t = std::time(nullptr);
        std::ostringstream oss;
        oss << "NONE_SN_" << std::put_time(std::localtime(&t), "%Y%m%d_%H%M%S");
        last_sn_ = oss.str();
      }
      ExternalTrigger trigger{};
      trigger.trigger_id = next_trigger_id_++;
      trigger.seat_id = last_sn_;
      trigger.sku = default_sku_;
      *out_trigger = trigger;
      std::cout << "距离信号: 触发 seat_id=" << trigger.seat_id
                << " trigger_id=" << trigger.trigger_id << std::endl;
      return true;
    }
    // 再尝试从上游获取 SN
    if (delegate_->wait_trigger(&sn_trigger, 50, nullptr)) {
      last_sn_ = sn_trigger.seat_id;
    }
  }

  if (error_message) *error_message = "距离传感器触发超时";
  return false;
}

bool DistanceTriggerSignalClient::publish_result(const ExternalTrigger& trigger,
                                                  std::uint64_t sequence_id,
                                                  InspectionDecision decision,
                                                  int timeout_ms,
                                                  std::string* error_message) {
  return delegate_->publish_result(trigger, sequence_id, decision, timeout_ms, error_message);
}

SignalHealth DistanceTriggerSignalClient::get_health() const {
  if (!delegate_) return SignalHealth{false, "no delegate"};
  auto health = delegate_->get_health();
  if (sensor_enabled_) {
    health.message = std::string("distance_trigger+") + health.message;
  }
  return health;
}

}  // namespace seat_aoi
