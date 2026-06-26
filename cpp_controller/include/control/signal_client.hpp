#pragma once

#include "control/isignal_client.hpp"

namespace seat_aoi {

class SimSignalClient : public ISignalClient {
public:
  bool initialize(const SignalClientConfig& config) override;
  bool wait_trigger(ExternalTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool publish_result(const ExternalTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message) override;
  SignalHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_output_fault_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 1000;
};

class ManualSignalClient : public ISignalClient {
public:
  bool initialize(const SignalClientConfig& config) override;
  bool wait_trigger(ExternalTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool publish_result(const ExternalTrigger& trigger,
                     std::uint64_t sequence_id,
                     InspectionDecision decision,
                     int timeout_ms,
                     std::string* error_message) override;
  SignalHealth get_health() const override;

private:
  bool initialized_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 9000;
  std::string station_id_ = "MANUAL_AOI";
  std::string sku_ = "seat_a_black_leather";
};

class ExternalSignalClient : public ISignalClient {
public:
  bool initialize(const SignalClientConfig& config) override;
  bool wait_trigger(ExternalTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;
  bool publish_result(const ExternalTrigger& trigger,
                      std::uint64_t sequence_id,
                      InspectionDecision decision,
                      int timeout_ms,
                      std::string* error_message) override;
  SignalHealth get_health() const override;
  bool is_idle_wait_timeout(const std::string& error_message) const override;

private:
  bool initialized_ = false;
  bool simulate_output_fault_ = false;
  bool simulate_trigger_timeout_ = false;
  std::uint64_t next_trigger_id_ = 1;
  std::size_t consumed_lines_ = 0;
  std::string station_id_ = "LINE_AOI_01";
  std::string default_seat_id_ = "EXTERNAL_SEAT";
  std::string default_sku_ = "seat_a_black_leather";
  std::string trigger_queue_path_;
  std::string result_queue_path_;
};

}  // namespace seat_aoi
