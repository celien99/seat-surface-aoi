#pragma once

#include "control/iplc_client.hpp"

namespace seat_aoi {

class SimPlcClient : public IPlcClient {
public:
  bool initialize(const PlcClientConfig& config) override;
  bool wait_trigger(PlcTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool send_decision(const PlcTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message) override;
  PlcHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_output_fault_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 1000;
};

class ManualTriggerPlcClient : public IPlcClient {
public:
  bool initialize(const PlcClientConfig& config) override;
  bool wait_trigger(PlcTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool send_decision(const PlcTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message) override;
  PlcHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 9000;
  std::string station_id_ = "MANUAL_AOI";
  std::string sku_ = "seat_a_black_leather";
};

}  // namespace seat_aoi
