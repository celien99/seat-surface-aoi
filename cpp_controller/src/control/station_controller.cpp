#include "control/station_controller.hpp"

#include <iostream>
#include <sstream>

#include "control/hardware_factory.hpp"

#include "common/string_utils.hpp"

namespace seat_aoi {

namespace {

PlcClientConfig make_plc_client_config(const RuntimePlcConfig& config) {
  PlcClientConfig client_config;
  client_config.host = config.host;
  client_config.port = config.port;
  client_config.station_id = config.station_id;
  client_config.trigger_source = config.trigger_source;
  client_config.trigger_id_source = config.trigger_id_source;
  client_config.seat_id_source = config.seat_id_source;
  client_config.sku_source = config.sku_source;
  client_config.ok_output = config.ok_output;
  client_config.ng_output = config.ng_output;
  client_config.recheck_output = config.recheck_output;
  client_config.ack_input = config.ack_input;
  client_config.output_hold_ms = config.output_hold_ms;
  client_config.simulate_output_fault = config.simulate_output_fault;
  client_config.simulate_trigger_timeout = config.simulate_trigger_timeout;
  return client_config;
}

}  // namespace

bool StationController::initialize(const StationConfig& config) {
  config_ = config;
  StationRuntimeConfig runtime_config;
  runtime_config.hardware_mode = config.hardware_mode;
  runtime_config.camera_backend = config.camera_backend;
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
  runtime_config.cameras = config.cameras;
  runtime_config.light = config.light;
  runtime_config.light_channels = config.light_channels;
  runtime_config.plc = config.plc;
  runtime_config.trigger_sync_mode = config.trigger_sync_mode;
  runtime_config.light.simulate_fault = config.simulate_light_fault;
  runtime_config.plc.simulate_output_fault = config.simulate_plc_output_fault;
  runtime_config.plc.simulate_trigger_timeout = config.simulate_trigger_timeout;
  for (auto& camera : runtime_config.cameras) {
    camera.simulate_missing_frame = config.simulate_missing_frame;
  }
  frame_assembler_.configure(runtime_config);
  plc_client_ = create_plc_client(config.plc.backend);
  if (!plc_client_->initialize(make_plc_client_config(runtime_config.plc))) {
    std::cerr << "PLC 初始化失败: " << plc_client_->get_health().message << std::endl;
    return false;
  }
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
  AcquisitionError acquisition_error;
  if (!frame_assembler_.acquire_bundles(
          recipe, trigger, sequence_id, &bundle, &acquisition_error)) {
    std::ostringstream oss;
    oss << acquisition_error.message << " stage="
        << static_cast<std::uint32_t>(acquisition_error.stage)
        << " camera_index=" << acquisition_error.camera_index
        << " light_index=" << acquisition_error.light_index
        << " light_seq_index=" << acquisition_error.light_seq_index;
    const ErrorCode error_code = acquisition_error.code == ErrorCode::None
                                     ? ErrorCode::InternalError
                                     : acquisition_error.code;
    return make_and_send_recheck_result(trigger, sequence_id, error_code, oss.str());
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
