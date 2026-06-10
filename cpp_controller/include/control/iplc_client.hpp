#pragma once

#include <cstdint>
#include <string>

#include "common/inspection_types.hpp"
#include "control/trigger_scheduler.hpp"

namespace seat_aoi {

struct PlcClientConfig {
  std::string host;
  std::uint32_t port = 0;
  std::string station_id;
  std::string trigger_source;
  std::string trigger_id_source;
  std::string seat_id_source;
  std::string sku_source;
  std::string ok_output;
  std::string ng_output;
  std::string recheck_output;
  std::string ack_input;
  std::uint32_t output_hold_ms = 200;
  bool simulate_output_fault = false;
  bool simulate_trigger_timeout = false;
};

struct PlcHealth {
  bool ok = true;
  std::string message = "simulated";
};

class IPlcClient {
public:
  virtual ~IPlcClient() = default;
  virtual bool initialize(const PlcClientConfig& config) = 0;
  virtual bool wait_trigger(PlcTrigger* out_trigger,
                            int timeout_ms,
                            std::string* error_message) = 0;
  virtual bool send_decision(const PlcTrigger& trigger,
                             std::uint64_t sequence_id,
                             InspectionDecision decision,
                             int timeout_ms,
                             std::string* error_message) = 0;
  virtual PlcHealth get_health() const = 0;
};

}  // namespace seat_aoi
