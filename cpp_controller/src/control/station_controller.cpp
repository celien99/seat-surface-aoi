#include "control/station_controller.hpp"

#include <iostream>

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
  runtime_config.camera_timeout_ms = config.camera_timeout_ms;
  runtime_config.light_timeout_ms = config.light_timeout_ms;
  runtime_config.light.simulate_fault = config.simulate_light_fault;
  runtime_config.plc.simulate_output_fault = config.simulate_plc_output_fault;
  for (auto& camera : runtime_config.cameras) {
    camera.simulate_missing_frame = config.simulate_missing_frame;
  }
  frame_assembler_.configure(runtime_config);
  plc_client_.initialize(config.simulate_plc_output_fault);
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

InspectionResultPayload StationController::inspect_one_seat(const PlcTrigger& trigger) {
  const std::uint64_t sequence_id = next_sequence_id_++;
  const Recipe recipe = load_recipe(trigger.sku);
  SeatImageBundle bundle;
  std::string error;
  if (!frame_assembler_.acquire_bundles(recipe, trigger, sequence_id, &bundle, &error)) {
    return make_recheck_result(trigger, sequence_id, ErrorCode::MissingFrame, error);
  }

  std::uint64_t published_sequence_id = 0;
  if (!frame_ring_.publish(bundle,
                           config_.publish_timeout_ms,
                           &published_sequence_id,
                           &error)) {
    return make_recheck_result(trigger, sequence_id, ErrorCode::SlotUnavailable, error);
  }

  InspectionResultPayload result;
  if (!result_ring_.wait_for_result(published_sequence_id,
                                    config_.detector_timeout_ms,
                                    &result,
                                    &error)) {
    return make_recheck_result(trigger, sequence_id, ErrorCode::DetectorTimeout, error);
  }
  const auto decision = static_cast<InspectionDecision>(result.meta.decision);
  if (!plc_client_.send_decision(trigger, sequence_id, decision, 200, &error)) {
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
  recipe.recipe_id = "seat_a_black_leather_v1";
  recipe.light_order = {1, 2, 3, 4};
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
