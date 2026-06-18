#pragma once

#include <cstdint>
#include <string>

#include "common/inspection_types.hpp"
#include "control/trigger_scheduler.hpp"

namespace seat_aoi {

struct SignalClientConfig {
  std::string station_id;
  std::string default_seat_id = "EXTERNAL_SEAT";
  std::string default_sku = "seat_a_black_leather";
  std::string trigger_queue_path;
  std::string result_queue_path;
  std::uint32_t port = 0;
  std::string delimiter;
  std::string terminator = "\n";
  std::string ok_response = "ok\n";
  // TCP 结果回传 (result_notify)
  std::string result_host;
  std::uint32_t result_port = 0;
  std::string result_prefix = "result";
  std::string result_delimiter = "|";
  std::string ok_text = "OK";
  std::string ng_text = "NG";
  std::string recheck_text = "RECHECK";
  std::string error_text = "ERROR";
  bool simulate_output_fault = false;
  bool simulate_trigger_timeout = false;
};

struct SignalHealth {
  bool ok = true;
  std::string message = "simulated";
};

class ISignalClient {
public:
  virtual ~ISignalClient() = default;
  virtual bool initialize(const SignalClientConfig& config) = 0;
  virtual bool wait_trigger(ExternalTrigger* out_trigger,
                            int timeout_ms,
                            std::string* error_message) = 0;
  virtual bool publish_result(const ExternalTrigger& trigger,
                             std::uint64_t sequence_id,
                             InspectionDecision decision,
                             int timeout_ms,
                             std::string* error_message) = 0;
  virtual SignalHealth get_health() const = 0;
};

}  // namespace seat_aoi
