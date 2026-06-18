#pragma once

#include <memory>
#include <string>

#include "control/isignal_client.hpp"
#include "control/distance_sensor.hpp"

namespace seat_aoi {

/// 距离传感器触发信号客户端。
/// 内部委托一个上游 ISignalClient（如 TcpSignalClient）接收 SN，
/// 通过距离传感器轮询执行消抖触发，触发时组装 ExternalTrigger。
class DistanceTriggerSignalClient final : public ISignalClient {
public:
  explicit DistanceTriggerSignalClient(std::unique_ptr<ISignalClient> delegate);
  ~DistanceTriggerSignalClient() override;

  bool initialize(const SignalClientConfig& config) override;
  bool wait_trigger(ExternalTrigger* out_trigger,
                    int timeout_ms, std::string* error_message) override;
  bool publish_result(const ExternalTrigger& trigger,
                      std::uint64_t sequence_id, InspectionDecision decision,
                      int timeout_ms, std::string* error_message) override;
  SignalHealth get_health() const override;

private:
  std::unique_ptr<ISignalClient> delegate_;
  DistanceSensor sensor_;
  DistanceSensorConfig sensor_config_{};

  bool sensor_enabled_ = false;
  bool initialized_ = false;
  std::string last_sn_;
  std::uint64_t next_trigger_id_ = 1;
  std::string station_id_ = "DIST_AOI";
  std::string default_sku_ = "seat_a_black_leather";
};

}  // namespace seat_aoi
