#include "control/frame_assembler.hpp"

#include <map>
#include <sstream>

#include "camera/camera_worker.hpp"
#include "control/hardware_factory.hpp"
#include "control/light_controller.hpp"

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"

namespace seat_aoi {

namespace {

void set_acquisition_error(AcquisitionError* error,
                           ErrorCode code,
                           AcquisitionStage stage,
                           std::uint32_t camera_index,
                           std::uint32_t light_index,
                           std::uint32_t light_seq_index,
                           const std::string& message) {
  if (error == nullptr) {
    return;
  }
  error->code = code;
  error->stage = stage;
  error->camera_index = camera_index;
  error->light_index = light_index;
  error->light_seq_index = light_seq_index;
  error->message = message;
}

LightControllerConfig make_light_controller_config(const RuntimeLightConfig& config) {
  LightControllerConfig controller_config;
  controller_config.device_id = config.device_id;
  controller_config.host = config.host;
  controller_config.port = config.port;
  controller_config.serial_port = config.serial_port;
  controller_config.baud_rate = config.baud_rate;
  controller_config.trigger_input_line = config.trigger_input_line;
  controller_config.response_mode = config.response_mode;
  controller_config.simulate_fault = config.simulate_fault;
  return controller_config;
}

bool uses_strobe_controller(const LightChannelParam& channel) {
  return channel.acquisition_mode == LightAcquisitionMode::Strobe;
}

}  // namespace

void FrameAssembler::configure(const StationRuntimeConfig& config) {
  config_ = config;
  reset_devices();
}

bool FrameAssembler::ensure_initialized() {
  if (initialized_) {
    return true;
  }
  if (light_controllers_.empty()) {
    if (config_.lights.empty()) {
      return false;
    }
    for (std::size_t ctrl_idx = 0; ctrl_idx < config_.lights.size(); ++ctrl_idx) {
      auto ctrl = create_light_controller(config_.lights[ctrl_idx].backend);
      if (!ctrl->initialize(make_light_controller_config(config_.lights[ctrl_idx]))) {
        reset_devices();
        return false;
      }
      light_controllers_.push_back(std::move(ctrl));
    }
  }
  if (!robot_client_) {
    robot_client_ = create_robot_client(config_.robot.backend);
  }
  RobotClientConfig robot_config;
  robot_config.backend = config_.robot.backend;
  robot_config.controller_id = config_.robot.controller_id;
  robot_config.host = config_.robot.host;
  robot_config.port = config_.robot.port;
  robot_config.ready_input = config_.robot.ready_input;
  robot_config.fault_input = config_.robot.fault_input;
  robot_config.start_output = config_.robot.start_output;
  robot_config.simulate_fault = config_.robot.simulate_fault;
  if (!robot_client_->initialize(robot_config)) {
    reset_devices();
    return false;
  }
  cameras_.clear();
  for (const auto& runtime_camera : config_.cameras) {
    CameraConfig config;
    config.camera_index = runtime_camera.camera_index;
    config.camera_id = runtime_camera.camera_id;
    config.serial_number = runtime_camera.serial_number;
    config.calibration_id = runtime_camera.calibration_id;
    config.width = runtime_camera.width;
    config.height = runtime_camera.height;
    config.channels = runtime_camera.channels;
    config.pixel_format = runtime_camera.pixel_format;
    config.trigger_line = runtime_camera.trigger_line;
    config.exposure_output_line = runtime_camera.exposure_output_line;
    config.buffer_count = runtime_camera.buffer_count;
    config.simulate_missing_frame = runtime_camera.simulate_missing_frame;
    auto camera = create_camera(config_.camera_backend);
    if (!camera->initialize(config)) {
      reset_devices();
      return false;
    }
    camera->start();
    cameras_.push_back(std::move(camera));
  }
  initialized_ = true;
  return true;
}

bool FrameAssembler::acquire_bundles(const Recipe& recipe,
                                     const ExternalTrigger& trigger,
                                     std::uint64_t sequence_id,
                                     SeatImageBundle* out_bundle,
                                     AcquisitionError* error) {
  if (!ensure_initialized()) {
    set_acquisition_error(error,
                          ErrorCode::DeviceFault,
                          AcquisitionStage::Initialize,
                          0,
                          0,
                          0,
                          "failed to initialize acquisition hardware");
    return false;
  }
  if (out_bundle == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::InternalError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "out_bundle is null");
    return false;
  }

  LightSequence sequence;
  if (!build_light_sequence(recipe, &sequence, error)) {
    return false;
  }
  std::vector<RuntimeCaptureViewConfig> capture_plan;
  if (!build_capture_plan(&capture_plan, error)) {
    return false;
  }

  SeatJobMeta job{};
  job.sequence_id = sequence_id;
  job.trigger_id = trigger.trigger_id;
  copy_cstr(job.seat_id, trigger.seat_id);
  copy_cstr(job.sku, trigger.sku);
  copy_cstr(job.recipe_id, recipe.recipe_id);
  job.view_count = static_cast<std::uint32_t>(capture_plan.size());
  job.capture_mode = static_cast<std::uint32_t>(config_.capture_mode);
  job.created_at_us = now_us();

  std::vector<CapturedFrame> frames;
  frames.reserve(capture_plan.size() * sequence.channels.size());

  bool acquired = false;
  if (config_.capture_schedule == CaptureSchedule::SharedLightParallel) {
    acquired = acquire_shared_light_parallel_frames(sequence, trigger, capture_plan, &frames, error);
  } else {
    acquired = acquire_view_serial_tdm_frames(sequence, trigger, capture_plan, &frames, error);
  }
  if (!acquired) {
    return false;
  }

  job.frame_count = static_cast<std::uint32_t>(frames.size());
  out_bundle->job_meta = job;
  out_bundle->frames = std::move(frames);
  if (!validate_serial_tdm_bundle(*out_bundle, sequence, capture_plan, error)) {
    reset_devices();
    return false;
  }
  return true;
}

bool FrameAssembler::prepare_light_sequence_for_view(const LightSequence& sequence,
                                                     std::uint64_t trigger_id,
                                                     const RuntimeCaptureViewConfig& view,
                                                     AcquisitionError* error) {
  for (std::size_t controller_index = 0; controller_index < light_controllers_.size();
       ++controller_index) {
    LightSequence controller_sequence;
    for (const auto& channel : sequence.channels) {
      if (uses_strobe_controller(channel) && channel.controller_index == controller_index) {
        controller_sequence.channels.push_back(channel);
      }
    }
    if (controller_sequence.channels.empty()) {
      continue;
    }
    if (!light_controllers_[controller_index]->prepare_sequence(
            controller_sequence,
            trigger_id,
            config_.light_timeout_ms,
            error != nullptr ? &error->message : nullptr)) {
      std::ostringstream oss;
      oss << "light sequence prepare failed pose_id=" << view.pose_id
          << " camera_index=" << view.camera_index
          << " controller_index=" << controller_index;
      const std::string detail =
          error != nullptr && !error->message.empty() ? error->message : oss.str();
      set_acquisition_error(error,
                            ErrorCode::LightFault,
                            AcquisitionStage::ConfigureLightSequence,
                            view.camera_index,
                            0,
                            0,
                            detail);
      reset_devices();
      return false;
    }
  }
  return true;
}

bool FrameAssembler::acquire_view_serial_tdm_frames(
    const LightSequence& sequence,
    const ExternalTrigger& trigger,
    const std::vector<RuntimeCaptureViewConfig>& capture_plan,
    std::vector<CapturedFrame>* frames,
    AcquisitionError* error) {
  if (frames == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::InternalError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "frames output is null");
    return false;
  }

  // 默认时分频闪方案：外层按检测视角串行，内层按光源串行。
  for (const auto& view : capture_plan) {
    RobotPoseStatus pose_status;
    if (!wait_robot_pose_ready(trigger, view, &pose_status, error)) {
      reset_devices();
      return false;
    }
    if (!prepare_light_sequence_for_view(sequence, trigger.trigger_id, view, error)) {
      return false;
    }

    for (std::uint32_t light_seq_index = 0;
         light_seq_index < sequence.channels.size();
         ++light_seq_index) {
      const auto light_param = sequence.channels[light_seq_index];
      if (!light_param.enabled) continue;

      if (!arm_view_camera(trigger, view, light_param, light_seq_index, error)) {
        reset_devices();
        return false;
      }
      if (uses_strobe_controller(light_param)) {
        if (!light_controllers_[light_param.controller_index]->trigger_channel(
                light_param, trigger.trigger_id, light_seq_index,
                config_.light_timeout_ms, error != nullptr ? &error->message : nullptr)) {
          const std::string detail = error != nullptr && !error->message.empty()
                                         ? error->message : "light channel trigger failed";
          set_acquisition_error(error, ErrorCode::LightFault, AcquisitionStage::TriggerLight,
                                view.camera_index, light_param.light_index, light_seq_index, detail);
          reset_devices();
          return false;
        }
      }

      CapturedFrame frame;
      if (!wait_view_light_frame(trigger, view, light_param, light_seq_index, pose_status,
                                 &frame, error)) {
        reset_devices();
        return false;
      }
      frames->push_back(std::move(frame));
    }
  }
  return true;
}

bool FrameAssembler::acquire_shared_light_parallel_frames(
    const LightSequence& sequence,
    const ExternalTrigger& trigger,
    const std::vector<RuntimeCaptureViewConfig>& capture_plan,
    std::vector<CapturedFrame>* frames,
    AcquisitionError* error) {
  if (frames == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::InternalError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "frames output is null");
    return false;
  }
  if (capture_plan.empty()) {
    set_acquisition_error(error,
                          ErrorCode::ConfigurationError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "shared light parallel capture requires at least one view");
    return false;
  }
  if (!prepare_light_sequence_for_view(sequence, trigger.trigger_id, capture_plan.front(), error)) {
    return false;
  }

  std::vector<RobotPoseStatus> pose_statuses(capture_plan.size());
  for (std::size_t view_index = 0; view_index < capture_plan.size(); ++view_index) {
    if (!wait_robot_pose_ready(trigger, capture_plan[view_index], &pose_statuses[view_index],
                               error)) {
      reset_devices();
      return false;
    }
  }

  std::vector<std::vector<CapturedFrame>> frames_by_view(
      capture_plan.size(), std::vector<CapturedFrame>(sequence.channels.size()));
  std::vector<std::vector<bool>> captured(
      capture_plan.size(), std::vector<bool>(sequence.channels.size(), false));

  // 共享光源并行方案：外层按光源串行；每路光源触发前先 arm 所有固定机位相机。
  for (std::uint32_t light_seq_index = 0;
       light_seq_index < sequence.channels.size();
       ++light_seq_index) {
    const auto light_param = sequence.channels[light_seq_index];
    if (!light_param.enabled) continue;

    for (const auto& view : capture_plan) {
      if (!arm_view_camera(trigger, view, light_param, light_seq_index, error)) {
        reset_devices();
        return false;
      }
    }
    if (uses_strobe_controller(light_param)) {
      if (!light_controllers_[light_param.controller_index]->trigger_channel(
              light_param, trigger.trigger_id, light_seq_index,
              config_.light_timeout_ms, error != nullptr ? &error->message : nullptr)) {
        const std::string detail = error != nullptr && !error->message.empty()
                                       ? error->message : "light channel trigger failed";
        set_acquisition_error(error,
                              ErrorCode::LightFault,
                              AcquisitionStage::TriggerLight,
                              capture_plan.front().camera_index,
                              light_param.light_index,
                              light_seq_index,
                              detail);
        reset_devices();
        return false;
      }
    }

    for (std::size_t view_index = 0; view_index < capture_plan.size(); ++view_index) {
      if (!wait_view_light_frame(trigger, capture_plan[view_index], light_param,
                                 light_seq_index, pose_statuses[view_index],
                                 &frames_by_view[view_index][light_seq_index], error)) {
        reset_devices();
        return false;
      }
      captured[view_index][light_seq_index] = true;
    }
  }

  // 共享光源并行只是物理调度优化；发布给 Python 的帧包仍保持视角优先顺序。
  for (std::size_t view_index = 0; view_index < capture_plan.size(); ++view_index) {
    for (std::uint32_t light_seq_index = 0;
         light_seq_index < sequence.channels.size();
         ++light_seq_index) {
      if (!captured[view_index][light_seq_index]) {
        set_acquisition_error(error,
                              ErrorCode::MissingFrame,
                              AcquisitionStage::WaitFrame,
                              capture_plan[view_index].camera_index,
                              sequence.channels[light_seq_index].light_index,
                              light_seq_index,
                              "shared light parallel capture missed an expected frame");
        reset_devices();
        return false;
      }
      frames->push_back(std::move(frames_by_view[view_index][light_seq_index]));
    }
  }
  return true;
}

bool FrameAssembler::arm_view_camera(const ExternalTrigger& trigger,
                                     const RuntimeCaptureViewConfig& view,
                                     const LightChannelParam& light_param,
                                     std::uint32_t light_seq_index,
                                     AcquisitionError* error) {
  auto* camera_ptr = camera_for_index(view.camera_index);
  if (camera_ptr == nullptr) {
    std::ostringstream oss;
    oss << "capture view references missing camera_index=" << view.camera_index
        << " pose_id=" << view.pose_id;
    set_acquisition_error(error,
                          ErrorCode::ConfigurationError,
                          AcquisitionStage::Configuration,
                          view.camera_index,
                          light_param.light_index,
                          light_seq_index,
                          oss.str());
    return false;
  }
  if (!camera_ptr->arm(trigger.trigger_id,
                       light_param,
                       light_seq_index,
                       config_.camera_timeout_ms)) {
    std::ostringstream oss;
    oss << "camera arm failed camera_index=" << view.camera_index
        << " light_index=" << light_param.light_index;
    set_acquisition_error(error,
                          ErrorCode::CameraFault,
                          AcquisitionStage::ArmCamera,
                          view.camera_index,
                          light_param.light_index,
                          light_seq_index,
                          oss.str());
    return false;
  }
  return true;
}

bool FrameAssembler::wait_view_light_frame(const ExternalTrigger& trigger,
                                           const RuntimeCaptureViewConfig& view,
                                           const LightChannelParam& light_param,
                                           std::uint32_t light_seq_index,
                                           const RobotPoseStatus& pose_status,
                                           CapturedFrame* out_frame,
                                           AcquisitionError* error) {
  auto* camera_ptr = camera_for_index(view.camera_index);
  if (camera_ptr == nullptr || out_frame == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::ConfigurationError,
                          AcquisitionStage::Configuration,
                          view.camera_index,
                          light_param.light_index,
                          light_seq_index,
                          "capture view references missing camera or output frame");
    return false;
  }
  if (!camera_ptr->wait_frame(trigger.trigger_id,
                              light_param,
                              light_seq_index,
                              out_frame,
                              config_.camera_timeout_ms)) {
    std::ostringstream oss;
    oss << "camera frame timeout pose_id=" << view.pose_id
        << " camera_index=" << view.camera_index
        << " light_index=" << light_param.light_index
        << " light_seq_index=" << light_seq_index;
    set_acquisition_error(error,
                          ErrorCode::MissingFrame,
                          AcquisitionStage::WaitFrame,
                          view.camera_index,
                          light_param.light_index,
                          light_seq_index,
                          oss.str());
    return false;
  }
  out_frame->meta.camera_index = view.camera_index;
  out_frame->meta.pose_index = view.pose_index;
  out_frame->meta.shot_id = pose_status.shot_id;
  out_frame->meta.robot_timestamp_us = pose_status.robot_timestamp_us;
  for (int index = 0; index < 3; ++index) {
    out_frame->meta.robot_tcp_xyz_mm[index] = pose_status.tcp_xyz_mm[index];
    out_frame->meta.robot_rpy_deg[index] = pose_status.rpy_deg[index];
  }
  copy_cstr(out_frame->meta.camera_id, view.camera_id);
  copy_cstr(out_frame->meta.pose_id, view.pose_id);
  copy_cstr(out_frame->meta.calibration_id, view.calibration_id);
  return true;
}

void FrameAssembler::reset_devices() {
  for (auto& ctrl : light_controllers_) {
    if (ctrl) {
      ctrl->shutdown_all();
    }
  }
  light_controllers_.clear();
  robot_client_.reset();
  cameras_.clear();
  initialized_ = false;
}

bool FrameAssembler::build_light_sequence(const Recipe& recipe,
                                          LightSequence* out_sequence,
                                          AcquisitionError* error) const {
  if (out_sequence == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::InternalError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "out_sequence is null");
    return false;
  }
  std::map<std::uint32_t, RuntimeLightChannelConfig> channel_configs;
  for (const auto& channel : config_.light_channels) {
    channel_configs[channel.light_index] = channel;
  }
  out_sequence->channels.clear();
  for (std::uint32_t light_seq_index = 0; light_seq_index < recipe.light_order.size();
       ++light_seq_index) {
    const std::uint32_t light_index = recipe.light_order[light_seq_index];
    const auto iter = channel_configs.find(light_index);
    if (iter == channel_configs.end()) {
      set_acquisition_error(error,
                            ErrorCode::ConfigurationError,
                            AcquisitionStage::Configuration,
                            0,
                            light_index,
                            light_seq_index,
                            "light_order references missing light channel config");
      return false;
    }
    const auto& configured = iter->second;
    if (configured.acquisition_mode == LightAcquisitionMode::Strobe &&
        configured.controller_index >= config_.lights.size()) {
      set_acquisition_error(error,
                            ErrorCode::ConfigurationError,
                            AcquisitionStage::Configuration,
                            0,
                            light_index,
                            light_seq_index,
                            "light channel references missing controller config");
      return false;
    }
    if (configured.exposure_us == 0 || configured.gain <= 0.0F ||
        (configured.acquisition_mode == LightAcquisitionMode::Strobe &&
         (configured.physical_channel == 0 || configured.strobe_width_us == 0 ||
          configured.current_percent <= 0.0F || configured.current_percent > 100.0F))) {
      set_acquisition_error(error,
                            ErrorCode::ConfigurationError,
                            AcquisitionStage::Configuration,
                            0,
                            light_index,
                            light_seq_index,
                            "light channel config is invalid");
      return false;
    }
    LightChannelParam param;
    param.controller_index = configured.controller_index;
    param.light_index = configured.light_index;
    param.physical_channel = configured.physical_channel;
    param.exposure_us = configured.exposure_us;
    param.strobe_width_us = configured.strobe_width_us;
    param.trigger_delay_us = configured.trigger_delay_us;
    param.gain = configured.gain;
    param.current_percent = configured.current_percent;
    param.acquisition_mode = configured.acquisition_mode;
    out_sequence->channels.push_back(param);
  }
  return true;
}

bool FrameAssembler::validate_serial_tdm_bundle(const SeatImageBundle& bundle,
                                                const LightSequence& sequence,
                                                const std::vector<RuntimeCaptureViewConfig>& views,
                                                AcquisitionError* error) const {
  const std::uint32_t expected_frames =
      static_cast<std::uint32_t>(views.size() * sequence.channels.size());
  if (bundle.job_meta.view_count != views.size() ||
      bundle.job_meta.frame_count != expected_frames ||
      bundle.frames.size() != expected_frames) {
    set_acquisition_error(error,
                          ErrorCode::MissingFrame,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "serial TDM bundle frame_count mismatch");
    return false;
  }

  std::map<std::uint32_t, RuntimeCameraConfig> cameras_by_index;
  for (const auto& camera : config_.cameras) {
    cameras_by_index[camera.camera_index] = camera;
  }

  std::size_t frame_index = 0;
  for (const auto& view : views) {
    const auto camera_iter = cameras_by_index.find(view.camera_index);
    if (camera_iter == cameras_by_index.end()) {
      set_acquisition_error(error,
                            ErrorCode::ConfigurationError,
                            AcquisitionStage::Configuration,
                            view.camera_index,
                            0,
                            0,
                            "capture view references missing camera");
      return false;
    }
    const auto& camera = camera_iter->second;
    for (std::uint32_t light_seq_index = 0; light_seq_index < sequence.channels.size();
         ++light_seq_index) {
      const auto& expected_light = sequence.channels[light_seq_index];
      const auto& frame = bundle.frames[frame_index];
      const auto& meta = frame.meta;
      if (meta.camera_index != view.camera_index ||
          meta.pose_index != view.pose_index ||
          meta.light_index != expected_light.light_index ||
          meta.light_seq_index != light_seq_index) {
        std::ostringstream oss;
        oss << "serial TDM order mismatch expected pose_index=" << view.pose_index
            << " pose_id=" << view.pose_id
            << " camera_index=" << view.camera_index
            << " light_index=" << expected_light.light_index
            << " light_seq_index=" << light_seq_index
            << " actual pose_index=" << meta.pose_index
            << " actual camera_index=" << meta.camera_index
            << " light_index=" << meta.light_index
            << " light_seq_index=" << meta.light_seq_index;
        set_acquisition_error(error,
                              ErrorCode::InvalidPayload,
                              AcquisitionStage::Configuration,
                              camera.camera_index,
                              expected_light.light_index,
                              light_seq_index,
                              oss.str());
        return false;
      }
      if (frame.bytes.empty() ||
          meta.width != camera.width ||
          meta.height != camera.height ||
          meta.channels != camera.channels ||
          meta.stride_bytes < meta.width * meta.channels) {
        std::ostringstream oss;
        oss << "serial TDM frame metadata invalid camera_index="
            << camera.camera_index << " light_index=" << expected_light.light_index;
        set_acquisition_error(error,
                              ErrorCode::InvalidPayload,
                              AcquisitionStage::Configuration,
                              camera.camera_index,
                              expected_light.light_index,
                              light_seq_index,
                              oss.str());
        return false;
      }
      const std::uint64_t minimum_size =
          static_cast<std::uint64_t>(meta.stride_bytes) * meta.height;
      if (frame.bytes.size() < minimum_size) {
        std::ostringstream oss;
        oss << "serial TDM frame payload too small camera_index="
            << camera.camera_index << " light_index=" << expected_light.light_index;
        set_acquisition_error(error,
                              ErrorCode::InvalidPayload,
                              AcquisitionStage::Configuration,
                              camera.camera_index,
                              expected_light.light_index,
                              light_seq_index,
                              oss.str());
        return false;
      }
      ++frame_index;
    }
  }
  return true;
}

bool FrameAssembler::build_capture_plan(std::vector<RuntimeCaptureViewConfig>* out_views,
                                         AcquisitionError* error) const {
  if (out_views == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::InternalError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "out_views is null");
    return false;
  }
  out_views->clear();
  if (!config_.capture_views.empty()) {
    *out_views = config_.capture_views;
    return true;
  }
  if (config_.capture_mode == CaptureMode::RobotFlyshot) {
    set_acquisition_error(error,
                          ErrorCode::ConfigurationError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "robot_flyshot capture mode requires explicit pose plan");
    return false;
  }
  for (const auto& camera : config_.cameras) {
    RuntimeCaptureViewConfig view;
    view.pose_index = camera.camera_index;
    view.pose_id = camera.camera_id;
    view.camera_index = camera.camera_index;
    view.camera_id = camera.camera_id;
    view.calibration_id = camera.calibration_id;
    out_views->push_back(view);
  }
  return !out_views->empty();
}

ICamera* FrameAssembler::camera_for_index(std::uint32_t camera_index) const {
  for (std::size_t index = 0; index < config_.cameras.size() && index < cameras_.size(); ++index) {
    if (config_.cameras[index].camera_index == camera_index) {
      return cameras_[index].get();
    }
  }
  return nullptr;
}

bool FrameAssembler::wait_robot_pose_ready(const ExternalTrigger& trigger,
                                           const RuntimeCaptureViewConfig& view,
                                           RobotPoseStatus* out_status,
                                           AcquisitionError* error) {
  if (out_status == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::InternalError,
                          AcquisitionStage::Configuration,
                          view.camera_index,
                          0,
                          0,
                          "out_status is null");
    return false;
  }
  RobotPoseRequest request;
  if (config_.capture_mode != CaptureMode::RobotFlyshot) {
    out_status->ready = true;
    out_status->fault = false;
    out_status->shot_id = trigger.trigger_id;
    out_status->robot_timestamp_us = 0;
    for (int index = 0; index < 3; ++index) {
      out_status->tcp_xyz_mm[index] = 0.0F;
      out_status->rpy_deg[index] = 0.0F;
    }
    out_status->message = "fixed camera mode";
    return true;
  }
  request.pose_index = view.pose_index;
  request.pose_id = view.pose_id;
  request.shot_id_source = view.shot_id_source;
  request.ready_input = view.robot_ready_input;
  request.fault_input = view.robot_fault_input;
  request.photo_trigger_input = view.photo_trigger_input;
  request.simulated_shot_id = view.simulated_shot_id;
  for (int index = 0; index < 3; ++index) {
    request.planned_tcp_xyz_mm[index] = view.robot_tcp_xyz_mm[index];
    request.planned_rpy_deg[index] = view.robot_rpy_deg[index];
  }
  std::string robot_error;
  if (!robot_client_->wait_pose_ready(trigger,
                                      request,
                                      config_.trigger_timeout_ms,
                                      out_status,
                                      &robot_error)) {
    std::ostringstream oss;
    oss << "robot pose not ready pose_id=" << view.pose_id
        << " camera_index=" << view.camera_index
        << " error=" << robot_error;
    set_acquisition_error(error,
                          ErrorCode::RobotFault,
                          AcquisitionStage::Configuration,
                          view.camera_index,
                          0,
                          0,
                          oss.str());
    return false;
  }
  if (!out_status->ready || out_status->fault) {
    std::ostringstream oss;
    oss << "robot pose status invalid pose_id=" << view.pose_id
        << " ready=" << out_status->ready
        << " fault=" << out_status->fault
        << " message=" << out_status->message;
    set_acquisition_error(error,
                          ErrorCode::RobotFault,
                          AcquisitionStage::Configuration,
                          view.camera_index,
                          0,
                          0,
                          oss.str());
    return false;
  }
  return true;
}

}  // namespace seat_aoi
