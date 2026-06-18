#include "control/station_controller.hpp"

#include <iostream>
#include <sstream>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include "control/image_writer.hpp"

#include "control/hardware_factory.hpp"

#include "common/string_utils.hpp"

namespace seat_aoi {

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
  runtime_config.warning_recheck_threshold = config.warning_recheck_threshold;
  runtime_config.critical_recheck_threshold = config.critical_recheck_threshold;
  runtime_config.max_jobs = config.max_jobs;
  runtime_config.recipe_id = config.recipe_id;
  runtime_config.trace_root = config.trace_root;
  runtime_config.light_order = config.light_order;
  runtime_config.capture_mode = config.capture_mode;
  runtime_config.cameras = config.cameras;
  runtime_config.lights = {config.light};
  runtime_config.light_channels = config.light_channels;
  runtime_config.capture_views = config.capture_views;
  runtime_config.signal = config.signal;
  runtime_config.robot = config.robot;
  runtime_config.trigger_sync_mode = config.trigger_sync_mode;
  if (runtime_config.lights.empty()) {
    runtime_config.lights.emplace_back();
  }
  runtime_config.lights[0].simulate_fault = config.simulate_light_fault;
  runtime_config.robot.simulate_fault = config.robot.simulate_fault;
  runtime_config.signal.simulate_output_fault = config.simulate_signal_result_fault;
  runtime_config.signal.simulate_trigger_timeout = config.simulate_trigger_timeout;
  for (auto& camera : runtime_config.cameras) {
    camera.simulate_missing_frame = config.simulate_missing_frame;
  }
  std::string trace_error;
  if (!event_log_.initialize(config.trace_root, &trace_error)) {
    std::cerr << "C++ 生产事件日志初始化失败: " << trace_error << std::endl;
    return false;
  }
  health_.configure(config.warning_recheck_threshold, config.critical_recheck_threshold);
  health_.transition_to(StationState::Initialized, "station controller initialized");
  record_system_event("station_initialized", ErrorCode::None, "station controller initialized");
  frame_assembler_.configure(runtime_config);
  signal_client_ = create_signal_client(config.signal.backend);
  if (!signal_client_->initialize(make_signal_client_config(runtime_config.signal))) {
    std::cerr << "外部信号客户端初始化失败: " << signal_client_->get_health().message
              << std::endl;
    health_.record_fault(ErrorCode::DeviceFault, signal_client_->get_health().message);
    health_.transition_to(StationState::Fault, signal_client_->get_health().message);
    record_system_event("station_initialize_failed",
                        ErrorCode::DeviceFault,
                        signal_client_->get_health().message);
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
  if (!frames_ok || !results_ok) {
    health_.record_fault(ErrorCode::ProtocolMismatch, "shared memory initialize failed");
    health_.transition_to(StationState::Fault, "shared memory initialize failed");
    record_system_event("station_initialize_failed",
                        ErrorCode::ProtocolMismatch,
                        "shared memory initialize failed");
    return false;
  }
  health_.transition_to(StationState::Ready, "station ready");
  record_system_event("station_ready", ErrorCode::None, "station ready");
  return true;
}

bool StationController::wait_for_trigger(ExternalTrigger* out_trigger, std::string* error_message) {
  const auto snapshot = health_.snapshot();
  if (snapshot.state == StationState::Fault) {
    if (error_message != nullptr) {
      *error_message = snapshot.alarm_message.empty()
                           ? "station is in fault state"
                           : snapshot.alarm_message;
    }
    record_system_event("trigger_wait_blocked_by_fault",
                        ErrorCode::DeviceFault,
                        error_message != nullptr ? *error_message : "station is in fault state");
    return false;
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
  const Recipe recipe = load_recipe(trigger.sku);
  record_event("inspection_start",
               trigger,
               sequence_id,
               InspectionDecision::Recheck,
               ErrorCode::None,
               "start serial_tdm inspection");
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

  // 图像落盘（发布到共享内存之前保存原始图像）
  if (config_.image_save.enabled && config_.image_save.save_original) {
    const std::string seat_id(bundle.job_meta.seat_id);
    for (const auto& frame : bundle.frames) {
      const std::string camera_id(frame.meta.camera_id);
      std::ostringstream path;
      path << config_.image_save.root_dir << "/" << seat_id << "/"
           << camera_id << "_" << frame.meta.timestamp_us << "_L"
           << frame.meta.light_index << "_original.pgm";
      std::string save_error;
      if (!write_pgm(path.str(), frame.bytes, frame.meta.width, frame.meta.height,
                     &save_error)) {
        std::cerr << "图像保存失败: " << save_error << std::endl;
      }
    }
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

  // JSON 详细结果输出 (detail_result_output)
  if (config_.json_output_enabled && !config_.json_output_host.empty() &&
      config_.json_output_port > 0) {
    std::ostringstream json;
    json << "{\"type\":\"inspection_result\","
         << "\"sn\":\"" << trigger.seat_id << "\","
         << "\"overall\":\"" << (decision == InspectionDecision::OK ? "OK"
                               : decision == InspectionDecision::NG ? "NG"
                               : "RECHECK") << "\","
         << "\"overall_code\":" << static_cast<std::uint32_t>(decision) << ","
         << "\"sequence\":" << sequence_id << ","
         << "\"error_code\":" << result.meta.error_code << ","
         << "\"elapsed_ms\":" << result.meta.elapsed_ms << ","
         << "\"defect_count\":" << result.meta.defect_count << "}\n";
    // 通过 TCP 发送（best-effort, 不阻塞主流程）
    try {
      // POSIX socket send
      int sock = ::socket(AF_INET, SOCK_STREAM, 0);
      if (sock >= 0) {
        struct sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_port = htons(static_cast<uint16_t>(config_.json_output_port));
        if (inet_pton(AF_INET, config_.json_output_host.c_str(), &addr.sin_addr) == 1) {
          if (::connect(sock, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) == 0) {
            const auto& data = json.str();
            ::send(sock, data.data(), data.size(), 0);
          }
        }
        ::close(sock);
      }
    } catch (...) {}
  }

  log_result(result);
  return result;
}

void StationController::cleanup_shared_memory() {
  health_.transition_to(StationState::Stopped, "shared memory cleanup requested");
  record_system_event("station_stopped", ErrorCode::None, "shared memory cleanup requested");
  frame_ring_.unlink_name();
  result_ring_.unlink_name();
  frame_ring_.close();
  result_ring_.close();
}

StationHealthSnapshot StationController::health_snapshot() const {
  return health_.snapshot();
}

Recipe StationController::load_recipe(const std::string& /*sku*/) const {
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
  if (!signal_client_->publish_result(trigger, sequence_id, InspectionDecision::Recheck, 200, &publish_error)) {
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
