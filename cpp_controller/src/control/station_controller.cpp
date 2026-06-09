#include "control/station_controller.hpp"

#include <iostream>

#include "control/plc_client.hpp"

#include "common/string_utils.hpp"

namespace seat_aoi {

bool StationController::initialize(const StationConfig& config) {
  config_ = config;
  StationRuntimeConfig runtime_config;
  runtime_config.reset_shared_memory = config.reset_shared_memory;
  runtime_config.slot_count = config.slot_count;
  runtime_config.frame_slot_size = config.frame_slot_size;
  runtime_config.result_slot_size = config.result_slot_size;
  runtime_config.publish_timeout_ms = config.publish_timeout_ms;
  runtime_config.detector_timeout_ms = config.detector_timeout_ms;
  runtime_config.trigger_timeout_ms = config.trigger_timeout_ms;
  runtime_config.camera_timeout_ms = config.camera_timeout_ms;
  runtime_config.light_timeout_ms = config.light_timeout_ms;
  runtime_config.max_jobs = config.max_jobs;
  runtime_config.recipe_id = config.recipe_id;
  runtime_config.light_order = config.light_order;
  runtime_config.trigger_sync_mode = config.trigger_sync_mode;
  runtime_config.light.simulate_fault = config.simulate_light_fault;
  runtime_config.plc.simulate_output_fault = config.simulate_plc_output_fault;
  runtime_config.plc.simulate_trigger_timeout = config.simulate_trigger_timeout;
  for (auto& camera : runtime_config.cameras) {
    camera.simulate_missing_frame = config.simulate_missing_frame;
  }
  frame_assembler_.configure(runtime_config);
  plc_client_ = std::make_unique<SimPlcClient>();
  plc_client_->initialize(config.simulate_plc_output_fault, config.simulate_trigger_timeout);
  const bool frames_ok = frame_ring_.initialize(kFrameShmName,
                                                config.slot_count,
                                                config.frame_slot_size,
                                                config.reset_shared_memory);
  const bool results_ok = result_ring_.initialize(kResultShmName,
                                                  config.slot_count,
                                                  config.result_slot_size,
                                                  config.reset_shared_memory);
  return frames_ok && results_ok;
}

bool StationController::wait_for_trigger(PlcTrigger* out_trigger, std::string* error_message) {
  return plc_client_->wait_trigger(out_trigger, config_.trigger_timeout_ms, error_message);
}

InspectionResultPayload StationController::inspect_one_seat(const PlcTrigger& trigger) {
  const std::uint64_t sequence_id = next_sequence_id_++;
  const Recipe recipe = load_recipe(trigger.sku);
  SeatImageBundle bundle;
  std::string error;
  if (!frame_assembler_.acquire_bundles(recipe, trigger, sequence_id, &bundle, &error)) {
    return make_and_send_recheck_result(trigger, sequence_id, ErrorCode::MissingFrame, error);
  }

  std::uint64_t published_sequence_id = 0;
  if (!frame_ring_.publish(bundle,
                           config_.publish_timeout_ms,
                           &published_sequence_id,
                           &error)) {
    return make_and_send_recheck_result(trigger, sequence_id, ErrorCode::SlotUnavailable, error);
  }

  InspectionResultPayload result;
  ErrorCode result_error_code = ErrorCode::None;
  if (!result_ring_.wait_for_result(published_sequence_id,
                                    config_.detector_timeout_ms,
                                    &result,
                                    &result_error_code,
                                    &error)) {
    if (result_error_code == ErrorCode::None) {
      result_error_code = ErrorCode::DetectorTimeout;
    }
    return make_and_send_recheck_result(trigger, sequence_id, result_error_code, error);
  }
  if (!validate_detector_result(trigger, published_sequence_id, result, &error)) {
    return make_and_send_recheck_result(trigger, sequence_id, ErrorCode::InvalidPayload, error);
  }
  const auto decision = static_cast<InspectionDecision>(result.meta.decision);
  if (!plc_client_->send_decision(trigger, sequence_id, decision, 200, &error)) {
    return make_recheck_result(trigger, sequence_id, ErrorCode::DeviceFault, error);
  }
  log_result(result);
  return result;
}

void StationController::cleanup_shared_memory() {
  frame_ring_.unlink_name();
  result_ring_.unlink_name();
}

Recipe StationController::load_recipe(const std::string& /*sku*/) const {
  Recipe recipe;
  recipe.recipe_id = config_.recipe_id;
  recipe.light_order = config_.light_order;
  return recipe;
}

InspectionResultPayload StationController::make_recheck_result(const PlcTrigger& trigger,
                                                               std::uint64_t sequence_id,
                                                               ErrorCode error_code,
                                                               const std::string& message) const {
  InspectionResultPayload result;
  result.meta.sequence_id = sequence_id;
  result.meta.trigger_id = trigger.trigger_id;
  copy_cstr(result.meta.seat_id, trigger.seat_id);
  result.meta.decision = static_cast<std::uint32_t>(InspectionDecision::Recheck);
  result.meta.defect_count = 0;
  result.meta.quality_pass = 0;
  result.meta.error_code = static_cast<std::uint32_t>(error_code);
  result.meta.elapsed_ms = 0.0F;
  std::cerr << "[sequence_id=" << sequence_id << " trigger_id=" << trigger.trigger_id
            << "] conservative RECHECK: " << message << std::endl;
  return result;
}

InspectionResultPayload StationController::make_and_send_recheck_result(
    const PlcTrigger& trigger,
    std::uint64_t sequence_id,
    ErrorCode error_code,
    const std::string& message) {
  auto result = make_recheck_result(trigger, sequence_id, error_code, message);
  std::string plc_error;
  if (!plc_client_->send_decision(trigger, sequence_id, InspectionDecision::Recheck, 200, &plc_error)) {
    result.meta.error_code = static_cast<std::uint32_t>(ErrorCode::DeviceFault);
    std::cerr << "[sequence_id=" << sequence_id << " trigger_id=" << trigger.trigger_id
              << "] PLC RECHECK output failed: " << plc_error << std::endl;
  }
  return result;
}

bool StationController::validate_detector_result(const PlcTrigger& trigger,
                                                 std::uint64_t sequence_id,
                                                 const InspectionResultPayload& result,
                                                 std::string* error_message) const {
  const auto set_error = [error_message](const std::string& message) {
    if (error_message != nullptr) {
      *error_message = message;
    }
  };
  if (result.meta.sequence_id != sequence_id) {
    set_error("detector result sequence_id mismatch");
    return false;
  }
  if (result.meta.trigger_id != trigger.trigger_id) {
    set_error("detector result trigger_id mismatch");
    return false;
  }
  if (fixed_cstr_to_string(result.meta.seat_id, kStringIdSize) != trigger.seat_id) {
    set_error("detector result seat_id mismatch");
    return false;
  }
  const auto decision = static_cast<InspectionDecision>(result.meta.decision);
  if (decision != InspectionDecision::OK &&
      decision != InspectionDecision::NG &&
      decision != InspectionDecision::Recheck &&
      decision != InspectionDecision::Error) {
    set_error("detector result decision is invalid");
    return false;
  }
  if (result.meta.defect_count != result.defects.size()) {
    set_error("detector result defect_count does not match payload");
    return false;
  }
  if (decision == InspectionDecision::OK) {
    if (result.meta.quality_pass == 0) {
      set_error("detector result OK with quality_pass=false");
      return false;
    }
    if (result.meta.error_code != static_cast<std::uint32_t>(ErrorCode::None)) {
      set_error("detector result OK with non-zero error_code");
      return false;
    }
    if (result.meta.defect_count != 0) {
      set_error("detector result OK with defects");
      return false;
    }
  } else if (result.meta.error_code == static_cast<std::uint32_t>(ErrorCode::None) &&
             decision == InspectionDecision::Error) {
    set_error("detector result ERROR with empty error_code");
    return false;
  }
  return true;
}

void StationController::log_result(const InspectionResultPayload& result) const {
  std::cout << "[sequence_id=" << result.meta.sequence_id
            << " trigger_id=" << result.meta.trigger_id
            << "] decision=" << result.meta.decision
            << " quality_pass=" << result.meta.quality_pass
            << " defects=" << result.meta.defect_count
            << " error_code=" << result.meta.error_code
            << " elapsed_ms=" << result.meta.elapsed_ms << std::endl;
}

}  // namespace seat_aoi
