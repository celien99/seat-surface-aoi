#include "control/station_controller.hpp"

#include <iostream>
#include <sstream>

#include "common/string_utils.hpp"
#include "control/hardware_factory.hpp"
#include "control/image_writer.hpp"

namespace seat_aoi {

StationConfig to_station_config(const StationRuntimeConfig& config) {
  StationConfig out;
  out.hardware_mode = config.hardware_mode;
  out.camera_backend = config.camera_backend;
  out.reset_shared_memory = config.reset_shared_memory;
  out.slot_count = config.slot_count;
  out.frame_slot_size = config.frame_slot_size;
  out.result_slot_size = config.result_slot_size;
  out.publish_timeout_ms = config.publish_timeout_ms;
  out.detector_timeout_ms = config.detector_timeout_ms;
  out.trigger_timeout_ms = config.trigger_timeout_ms;
  out.camera_timeout_ms = config.camera_timeout_ms;
  out.light_timeout_ms = config.light_timeout_ms;
  out.arm_settle_ms = config.arm_settle_ms;
  out.warning_recheck_threshold = config.warning_recheck_threshold;
  out.critical_recheck_threshold = config.critical_recheck_threshold;
  out.max_jobs = config.max_jobs;
  out.recipe_id = config.recipe_id;
  out.trace_root = config.trace_root;
  out.light_order = config.light_order;
  out.controller_mode = config.controller_mode;
  out.capture_mode = config.capture_mode;
  out.capture_schedule = config.capture_schedule;
  out.cameras = config.cameras;
  out.light = config.lights.empty() ? RuntimeLightConfig{} : config.lights[0];
  out.lights = config.lights;
  out.light_channels = config.light_channels;
  out.signal = config.signal;
  out.simulate_light_fault = !config.lights.empty() && config.lights[0].simulate_fault;
  out.simulate_trigger_timeout = config.signal.simulate_trigger_timeout;
  out.simulate_signal_result_fault = config.signal.simulate_output_fault;
  for (const auto& camera : config.cameras) {
    out.simulate_missing_frame = out.simulate_missing_frame || camera.simulate_missing_frame;
  }
  out.image_save = config.image_save;
  return out;
}

namespace {

SignalClientConfig make_signal_client_config(const RuntimeSignalConfig& config) {
  SignalClientConfig client_config;
  client_config.station_id = config.station_id;
  client_config.default_seat_id = config.default_seat_id;
  client_config.default_sku = config.default_sku;
  client_config.trigger_queue_path = config.trigger_queue_path;
  client_config.result_queue_path = config.result_queue_path;
  client_config.port = config.port;
  client_config.delimiter = config.delimiter;
  client_config.terminator = config.terminator;
  client_config.ok_response = config.ok_response;
  client_config.result_host = config.result_host;
  client_config.result_port = config.result_port;
  client_config.result_prefix = config.result_prefix;
  client_config.result_delimiter = config.result_delimiter;
  client_config.ok_text = config.ok_text;
  client_config.ng_text = config.ng_text;
  client_config.recheck_text = config.recheck_text;
  client_config.error_text = config.error_text;
  client_config.simulate_output_fault = config.simulate_output_fault;
  client_config.simulate_trigger_timeout = config.simulate_trigger_timeout;
  return client_config;
}

bool save_original_images(const ImageSaveConfig& config,
                          const SeatImageBundle& bundle,
                          bool force_fail_on_error,
                          std::string* error_message) {
  if (!config.enabled || !config.save_original) {
    return true;
  }

  const std::string date_dir = image_save_date_dir();
  const std::string seat_id = fixed_cstr_to_string(bundle.job_meta.seat_id, kStringIdSize);
  for (const auto& frame : bundle.frames) {
    const std::string path = build_original_image_path(config, date_dir, seat_id, frame);
    std::string save_error;
    if (!write_pgm(path, frame.bytes, frame.meta.width, frame.meta.height, &save_error)) {
      const std::string message = "image save failed: " + save_error;
      if (force_fail_on_error || config.fail_on_save_error) {
        if (error_message != nullptr) {
          *error_message = message;
        }
        return false;
      }
      std::cerr << message << std::endl;
    }
  }
  return true;
}

}  // namespace

StationController::~StationController() {
  frame_ring_.close();
  result_ring_.close();
}

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
  runtime_config.arm_settle_ms = config.arm_settle_ms;
  runtime_config.warning_recheck_threshold = config.warning_recheck_threshold;
  runtime_config.critical_recheck_threshold = config.critical_recheck_threshold;
  runtime_config.max_jobs = config.max_jobs;
  runtime_config.recipe_id = config.recipe_id;
  runtime_config.trace_root = config.trace_root;
  runtime_config.light_order = config.light_order;
  runtime_config.controller_mode = config.controller_mode;
  runtime_config.capture_mode = config.capture_mode;
  runtime_config.capture_schedule = config.capture_schedule;
  runtime_config.cameras = config.cameras;
  runtime_config.lights =
      config.lights.empty() ? std::vector<RuntimeLightConfig>{config.light} : config.lights;
  runtime_config.light_channels = config.light_channels;
  runtime_config.signal = config.signal;
  runtime_config.image_save = config.image_save;
  // 传播 CLI 模拟标志到运行时配置
  if (runtime_config.lights.empty()) {
    runtime_config.lights.emplace_back();
  }
  if (config.simulate_light_fault) {
    for (auto& light : runtime_config.lights) {
      light.simulate_fault = true;
    }
  }
  runtime_config.signal.simulate_output_fault = config.simulate_signal_result_fault;
  runtime_config.signal.simulate_trigger_timeout = config.simulate_trigger_timeout;
  for (auto& camera : runtime_config.cameras) {
    camera.simulate_missing_frame = config.simulate_missing_frame;
  }

  std::string trace_error;
  if (!event_log_.initialize(config.trace_root, &trace_error)) {
    std::cerr << "C++ production event log initialize failed: " << trace_error << std::endl;
    return false;
  }
  health_.configure(config.warning_recheck_threshold, config.critical_recheck_threshold);
  health_.transition_to(StationState::Initialized, "station controller initialized");
  record_system_event("station_initialized", ErrorCode::None, "station controller initialized");

  frame_assembler_.configure(runtime_config);
  signal_client_ = create_signal_client(config.signal.backend);
  if (!signal_client_->initialize(make_signal_client_config(runtime_config.signal))) {
    std::cerr << "external signal client initialize failed: "
              << signal_client_->get_health().message << std::endl;
    health_.record_fault(ErrorCode::DeviceFault, signal_client_->get_health().message);
    health_.transition_to(StationState::Fault, signal_client_->get_health().message);
    record_system_event("station_initialize_failed",
                        ErrorCode::DeviceFault,
                        signal_client_->get_health().message);
    return false;
  }

  shared_memory_initialized_ = false;
  if (config.controller_mode == ControllerMode::Online) {
    const bool frames_ok = frame_ring_.initialize(kFrameShmName,
                                                  config.slot_count,
                                                  config.frame_slot_size,
                                                  config.reset_shared_memory);
    const bool results_ok = result_ring_.initialize(kResultShmName,
                                                    config.slot_count,
                                                    config.result_slot_size,
                                                    config.reset_shared_memory);
    if (!frames_ok || !results_ok) {
      health_.record_fault(ErrorCode::ProtocolMismatch, "shared memory initialize failed");
      health_.transition_to(StationState::Fault, "shared memory initialize failed");
      record_system_event("station_initialize_failed",
                          ErrorCode::ProtocolMismatch,
                          "shared memory initialize failed");
      return false;
    }
    shared_memory_initialized_ = true;
  }

  const std::string ready_message =
      config.controller_mode == ControllerMode::CaptureOnly
          ? "station ready in capture_only mode"
          : "station ready";
  health_.transition_to(StationState::Ready, ready_message);
  record_system_event("station_ready", ErrorCode::None, ready_message);
  return true;
}

bool StationController::wait_for_trigger(ExternalTrigger* out_trigger, std::string* error_message) {
  const auto snapshot = health_.snapshot();
  if (snapshot.state == StationState::Fault) {
    // 自动尝试从 Fault 恢复（trigger timeout 等可恢复故障），
    // 避免一次 PLC 通信抖动导致进程永久阻塞。
    health_.transition_to(StationState::Ready,
                          "auto-recovery from fault: " + snapshot.alarm_message);
    record_system_event("trigger_wait_fault_recovery_attempt",
                        ErrorCode::None,
                        "recovering from fault: " + snapshot.alarm_message);
    // 继续执行，尝试等待触发信号
  }
  if (!signal_client_->wait_trigger(out_trigger, config_.trigger_timeout_ms, error_message)) {
    const std::string message =
        error_message != nullptr ? *error_message : "external signal trigger wait failed";
    health_.record_fault(ErrorCode::DeviceFault, message);
    health_.transition_to(StationState::Fault, message);
    record_system_event("trigger_wait_failed", ErrorCode::DeviceFault, message);
    return false;
  }
  health_.transition_to(StationState::Running, "trigger accepted");
  return true;
}

InspectionResultPayload StationController::inspect_one_seat(const ExternalTrigger& trigger) {
  const std::uint64_t sequence_id = next_sequence_id_++;
  const Recipe recipe = load_recipe();
  record_event("inspection_start",
               trigger,
               sequence_id,
               InspectionDecision::Recheck,
               ErrorCode::None,
               "start shared_light_parallel capture");

  std::string storage_message;
  if (!cleanup_runtime_storage_if_needed(config_.image_save, config_.trace_root, &storage_message)) {
    return make_and_send_recheck_result(
        trigger, sequence_id, ErrorCode::DeviceFault, storage_message);
  }
  if (!storage_message.empty()) {
    record_event("storage_cleanup",
                 trigger,
                 sequence_id,
                 InspectionDecision::Recheck,
                 ErrorCode::None,
                 storage_message);
  }
  storage_message.clear();
  if (!runtime_storage_has_required_free_ratio(config_.image_save,
                                               config_.trace_root,
                                               &storage_message)) {
    return make_and_send_recheck_result(
        trigger, sequence_id, ErrorCode::DeviceFault, storage_message);
  }

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
    const ErrorCode error_code =
        acquisition_error.code == ErrorCode::None ? ErrorCode::InternalError
                                                  : acquisition_error.code;
    return make_and_send_recheck_result(trigger, sequence_id, error_code, oss.str());
  }

  if (!save_original_images(config_.image_save,
                            bundle,
                            config_.controller_mode == ControllerMode::CaptureOnly,
                            &error)) {
    return make_and_send_recheck_result(trigger, sequence_id, ErrorCode::DeviceFault, error);
  }

  if (config_.controller_mode == ControllerMode::CaptureOnly) {
    const std::string message =
        "capture_only saved images; shared memory and detector bypassed";
    auto result = make_recheck_result(trigger, sequence_id, ErrorCode::None, message);
    if (!signal_client_->publish_result(trigger,
                                        sequence_id,
                                        InspectionDecision::Recheck,
                                        200,
                                        &error)) {
      result.meta.error_code = static_cast<std::uint32_t>(ErrorCode::DeviceFault);
      record_result_health(result, message + "; publish_error=" + error);
      record_event("capture_only_result_publish_failed",
                   trigger,
                   sequence_id,
                   InspectionDecision::Recheck,
                   ErrorCode::DeviceFault,
                   message + "; publish_error=" + error);
      return result;
    }
    health_.transition_to(StationState::Ready, "station ready in capture_only mode");
    record_event("capture_only_complete",
                 trigger,
                 sequence_id,
                 InspectionDecision::Recheck,
                 ErrorCode::None,
                 message);
    log_result(result);
    return result;
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
  const InspectionDecision published_decision =
      decision == InspectionDecision::Error ? InspectionDecision::Recheck : decision;
  if (!signal_client_->publish_result(trigger, sequence_id, published_decision, 200, &error)) {
    auto recheck = make_recheck_result(trigger, sequence_id, ErrorCode::DeviceFault, error);
    record_result_health(recheck, error);
    record_event("signal_result_publish_failed",
                 trigger,
                 sequence_id,
                 InspectionDecision::Recheck,
                 ErrorCode::DeviceFault,
                 error);
    return recheck;
  }
  record_result_health(result, "detector result accepted and external signal result published");
  record_event("inspection_complete",
               trigger,
               sequence_id,
               decision,
               static_cast<ErrorCode>(result.meta.error_code),
               published_decision == decision
                   ? "detector result accepted and external signal result published"
                   : "detector ERROR mapped to external RECHECK result");

  log_result(result);
  return result;
}

void StationController::cleanup_shared_memory() {
  health_.transition_to(StationState::Stopped, "shared memory cleanup requested");
  record_system_event("station_stopped", ErrorCode::None, "shared memory cleanup requested");
  if (shared_memory_initialized_) {
    frame_ring_.unlink_name();
    result_ring_.unlink_name();
    shared_memory_initialized_ = false;
  }
  frame_ring_.close();
  result_ring_.close();
}

StationHealthSnapshot StationController::health_snapshot() const {
  return health_.snapshot();
}

Recipe StationController::load_recipe() const {
  Recipe recipe;
  recipe.recipe_id = config_.recipe_id;
  recipe.light_order = config_.light_order;
  return recipe;
}

InspectionResultPayload StationController::make_recheck_result(const ExternalTrigger& trigger,
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
    const ExternalTrigger& trigger,
    std::uint64_t sequence_id,
    ErrorCode error_code,
    const std::string& message) {
  auto result = make_recheck_result(trigger, sequence_id, error_code, message);
  std::string publish_error;
  if (!signal_client_->publish_result(
          trigger, sequence_id, InspectionDecision::Recheck, 200, &publish_error)) {
    result.meta.error_code = static_cast<std::uint32_t>(ErrorCode::DeviceFault);
    record_result_health(result, message + "; publish_error=" + publish_error);
    std::cerr << "[sequence_id=" << sequence_id << " trigger_id=" << trigger.trigger_id
              << "] external RECHECK result publish failed: " << publish_error << std::endl;
    record_event("recheck_output_failed",
                 trigger,
                 sequence_id,
                 InspectionDecision::Recheck,
                 ErrorCode::DeviceFault,
                 message + "; publish_error=" + publish_error);
    return result;
  }
  record_result_health(result, message);
  record_event("inspection_recheck",
               trigger,
               sequence_id,
               InspectionDecision::Recheck,
               static_cast<ErrorCode>(result.meta.error_code),
               message);
  return result;
}

bool StationController::validate_detector_result(const ExternalTrigger& trigger,
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
  } else if (decision == InspectionDecision::NG) {
    if (result.meta.quality_pass == 0) {
      set_error("detector result NG with quality_pass=false");
      return false;
    }
    if (result.meta.error_code != static_cast<std::uint32_t>(ErrorCode::None)) {
      set_error("detector result NG with non-zero error_code");
      return false;
    }
    if (result.meta.defect_count == 0) {
      set_error("detector result NG without defects");
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
            << "] decision=" << inspection_decision_name(
                   static_cast<InspectionDecision>(result.meta.decision))
            << " quality_pass=" << result.meta.quality_pass
            << " defects=" << result.meta.defect_count
            << " error_code=" << error_code_name(
                   static_cast<ErrorCode>(result.meta.error_code))
            << " elapsed_ms=" << result.meta.elapsed_ms << std::endl;
}

void StationController::record_event(const std::string& name,
                                     const ExternalTrigger& trigger,
                                     std::uint64_t sequence_id,
                                     InspectionDecision decision,
                                     ErrorCode error_code,
                                     const std::string& message) {
  ProductionEvent event;
  event.name = name;
  event.sequence_id = sequence_id;
  event.trigger_id = trigger.trigger_id;
  event.seat_id = trigger.seat_id;
  event.sku = trigger.sku;
  event.decision = decision;
  event.error_code = error_code;
  event.health = health_.snapshot();
  event.message = message;
  event_log_.record(event);
}

void StationController::record_system_event(const std::string& name,
                                            ErrorCode error_code,
                                            const std::string& message) {
  ExternalTrigger trigger;
  trigger.trigger_id = 0;
  trigger.seat_id = "";
  trigger.sku = "";
  record_event(name, trigger, 0, InspectionDecision::Recheck, error_code, message);
}

void StationController::record_result_health(const InspectionResultPayload& result,
                                             const std::string& message) {
  const auto decision = static_cast<InspectionDecision>(result.meta.decision);
  const auto error_code = static_cast<ErrorCode>(result.meta.error_code);
  health_.record_result(decision, error_code, message);
  if (health_.snapshot().state != StationState::Fault) {
    health_.transition_to(StationState::Ready, "station ready");
  }
}

}  // namespace seat_aoi
