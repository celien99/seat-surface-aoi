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

const char* trigger_sync_mode_name(TriggerSyncMode mode) {
  switch (mode) {
    case TriggerSyncMode::Software:
      return "software";
    case TriggerSyncMode::CameraExposureOutput:
      return "camera_exposure_output";
  }
  return "unknown";
}

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
  controller_config.simulate_fault = config.simulate_fault;
  return controller_config;
}

}  // namespace

void FrameAssembler::configure(const StationRuntimeConfig& config) {
  config_ = config;
  initialized_ = false;
  cameras_.clear();
}

bool FrameAssembler::ensure_initialized() {
  if (initialized_) {
    return true;
  }
  if (!light_controller_) {
    light_controller_ = create_light_controller(config_.light.backend);
  }
  if (!light_controller_->initialize(make_light_controller_config(config_.light))) {
    return false;
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
      return false;
    }
    camera->start();
    cameras_.push_back(std::move(camera));
  }
  initialized_ = true;
  return true;
}

bool FrameAssembler::acquire_bundles(const Recipe& recipe,
                                     const PlcTrigger& trigger,
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
                          "failed to initialize simulated acquisition");
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

  // 时分频闪方案：外层按检测视角串行，内层按光源串行。
  // fixed_camera 模式下视角等同于固定机位；robot_flyshot 模式下视角等同于机器人 pose。
  for (const auto& view : capture_plan) {
    auto* camera_ptr = camera_for_index(view.camera_index);
    if (camera_ptr == nullptr) {
      std::ostringstream oss;
      oss << "capture view references missing camera_index=" << view.camera_index
          << " pose_id=" << view.pose_id;
      set_acquisition_error(error,
                            ErrorCode::ConfigurationError,
                            AcquisitionStage::Configuration,
                            view.camera_index,
                            0,
                            0,
                            oss.str());
      return false;
    }
    auto& camera = *camera_ptr;

    RobotPoseStatus pose_status;
    if (!wait_robot_pose_ready(trigger, view, &pose_status, error)) {
      light_controller_->shutdown_all();
      initialized_ = false;
      cameras_.clear();
      return false;
    }

    // 每个视角开始前重新准备光源序列
    if (!light_controller_->prepare_sequence(sequence,
                                            trigger.trigger_id,
                                            config_.light_timeout_ms,
                                            error != nullptr ? &error->message : nullptr)) {
      std::ostringstream oss;
      oss << "simulated light sequence prepare failed pose_id=" << view.pose_id
          << " camera_index=" << view.camera_index;
      const std::string detail =
          error != nullptr && !error->message.empty() ? error->message : oss.str();
      set_acquisition_error(error,
                            ErrorCode::LightFault,
                            AcquisitionStage::ConfigureLightSequence,
                            view.camera_index,
                            0,
                            0,
                            detail);
      light_controller_->shutdown_all();
      initialized_ = false;
      cameras_.clear();
      return false;
    }

    for (std::uint32_t light_seq_index = 0; light_seq_index < sequence.channels.size();
         ++light_seq_index) {
      const auto light_param = sequence.channels[light_seq_index];

      if (config_.trigger_sync_mode == TriggerSyncMode::Software) {
        // 软件触发：直接触发光源频闪
        if (!light_controller_->trigger_channel(light_param,
                                               trigger.trigger_id,
                                               light_seq_index,
                                               config_.light_timeout_ms,
                                               error != nullptr ? &error->message : nullptr)) {
          const std::string detail =
              error != nullptr && !error->message.empty() ? error->message
                                                          : "simulated light channel failed";
          set_acquisition_error(error,
                                ErrorCode::LightFault,
                                AcquisitionStage::TriggerLight,
                                view.camera_index,
                                light_param.light_index,
                                light_seq_index,
                                detail);
          light_controller_->shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }
      } else if (config_.trigger_sync_mode == TriggerSyncMode::CameraExposureOutput) {
        // ① 光源 arm — 进入预就绪状态
        if (!light_controller_->arm_hardware_trigger(light_param,
                                                    trigger.trigger_id,
                                                    light_seq_index,
                                                    config_.light_timeout_ms,
                                                    error != nullptr ? &error->message : nullptr)) {
          const std::string detail =
              error != nullptr && !error->message.empty()
                  ? error->message
                  : "simulated light hardware trigger arm failed";
          set_acquisition_error(error,
                                ErrorCode::LightFault,
                                AcquisitionStage::ArmLight,
                                view.camera_index,
                                light_param.light_index,
                                light_seq_index,
                                detail);
          light_controller_->shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }

        // ② 当前机位相机 arm — 等待曝光
        if (!camera.arm(trigger.trigger_id,
                        light_param,
                        light_seq_index,
                        config_.camera_timeout_ms)) {
          std::ostringstream oss;
          oss << "simulated camera arm failed camera_index=" << view.camera_index
              << " light_index=" << light_param.light_index
              << " light_seq_index=" << light_seq_index;
          set_acquisition_error(error,
                                ErrorCode::CameraFault,
                                AcquisitionStage::ArmCamera,
                                view.camera_index,
                                light_param.light_index,
                                light_seq_index,
                                oss.str());
          light_controller_->shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }

        // ③ 当前机位相机模拟曝光输出 → 触发光源频闪
        if (!camera.simulate_exposure_output(trigger.trigger_id,
                                             light_param,
                                             light_seq_index,
                                             config_.camera_timeout_ms)) {
          std::ostringstream oss;
          oss << "simulated camera exposure output failed camera_index=" << view.camera_index
              << " light_index=" << light_param.light_index
              << " light_seq_index=" << light_seq_index;
          set_acquisition_error(error,
                                ErrorCode::TriggerSyncFault,
                                AcquisitionStage::ExposureOutput,
                                view.camera_index,
                                light_param.light_index,
                                light_seq_index,
                                oss.str());
          light_controller_->shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }

        // ④ 通知光源硬件触发完成
        if (!light_controller_->notify_hardware_triggered(light_param,
                                                         trigger.trigger_id,
                                                         light_seq_index,
                                                         config_.light_timeout_ms,
                                                         error != nullptr ? &error->message : nullptr)) {
          const std::string detail =
              error != nullptr && !error->message.empty()
                  ? error->message
                  : "simulated light hardware trigger failed";
          set_acquisition_error(error,
                                ErrorCode::TriggerSyncFault,
                                AcquisitionStage::ConfirmLightTrigger,
                                view.camera_index,
                                light_param.light_index,
                                light_seq_index,
                                detail);
          light_controller_->shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }
      } else {
        set_acquisition_error(error,
                              ErrorCode::ConfigurationError,
                              AcquisitionStage::Configuration,
                              view.camera_index,
                              light_param.light_index,
                              light_seq_index,
                              std::string("unsupported trigger sync mode ") +
                                  trigger_sync_mode_name(config_.trigger_sync_mode));
        light_controller_->shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }

      // ⑤ 当前机位单相机采集（串行，不使用 std::async）
      CapturedFrame frame;
      if (!camera.wait_frame(trigger.trigger_id,
                             light_param,
                             light_seq_index,
                             &frame,
                             config_.camera_timeout_ms)) {
        std::ostringstream oss;
        oss << "simulated camera frame timeout pose_id=" << view.pose_id
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
        light_controller_->shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }
      frame.meta.camera_index = view.camera_index;
      frame.meta.pose_index = view.pose_index;
      frame.meta.shot_id = pose_status.shot_id;
      frame.meta.robot_timestamp_us = pose_status.robot_timestamp_us;
      for (int index = 0; index < 3; ++index) {
        frame.meta.robot_tcp_xyz_mm[index] = pose_status.tcp_xyz_mm[index];
        frame.meta.robot_rpy_deg[index] = pose_status.rpy_deg[index];
      }
      copy_cstr(frame.meta.camera_id, view.camera_id);
      copy_cstr(frame.meta.pose_id, view.pose_id);
      copy_cstr(frame.meta.calibration_id, view.calibration_id);
      frames.push_back(std::move(frame));
    }
  }

  job.frame_count = static_cast<std::uint32_t>(frames.size());
  out_bundle->job_meta = job;
  out_bundle->frames = std::move(frames);
  if (!validate_serial_tdm_bundle(*out_bundle, sequence, capture_plan, error)) {
    light_controller_->shutdown_all();
    initialized_ = false;
    cameras_.clear();
    return false;
  }
  return true;
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
    if (configured.physical_channel == 0 || configured.exposure_us == 0 ||
        configured.strobe_width_us == 0 || configured.gain <= 0.0F ||
        configured.current_percent <= 0.0F || configured.current_percent > 100.0F) {
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
    param.light_index = configured.light_index;
    param.physical_channel = configured.physical_channel;
    param.exposure_us = configured.exposure_us;
    param.strobe_width_us = configured.strobe_width_us;
    param.trigger_delay_us = configured.trigger_delay_us;
    param.gain = configured.gain;
    param.current_percent = configured.current_percent;
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

bool FrameAssembler::wait_robot_pose_ready(const PlcTrigger& trigger,
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
