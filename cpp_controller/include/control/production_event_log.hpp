#pragma once

#include <cstdint>
#include <fstream>
#include <mutex>
#include <string>

#include "common/error_code.hpp"
#include "common/inspection_types.hpp"
#include "control/station_health.hpp"

namespace seat_aoi {

struct ProductionEvent {
  std::string name;
  std::uint64_t sequence_id = 0;
  std::uint64_t trigger_id = 0;
  std::string seat_id;
  std::string sku;
  InspectionDecision decision = InspectionDecision::Recheck;
  ErrorCode error_code = ErrorCode::None;
  StationHealthSnapshot health;
  std::string message;
};

class ProductionEventLog {
public:
  bool initialize(const std::string& trace_root, std::string* error_message);
  void record(const ProductionEvent& event);
  bool enabled() const { return enabled_; }

private:
  void rotate_if_needed();

  std::mutex mutex_;
  std::ofstream output_;
  std::string trace_root_;
  bool enabled_ = false;

  static constexpr std::uintmax_t kMaxEventLogBytes = 50ULL * 1024 * 1024;
  static constexpr int kMaxRotatedLogs = 5;
};

const char* inspection_decision_name(InspectionDecision decision);
const char* error_code_name(ErrorCode error_code);

}  // namespace seat_aoi
