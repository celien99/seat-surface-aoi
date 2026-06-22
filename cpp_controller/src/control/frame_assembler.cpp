#include "control/frame_assembler.hpp"

#include <chrono>
#include <condition_variable>
#include <future>
#include <iostream>
#include <map>
#include <mutex>
#include <sstream>
#include <thread>

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"
#include "control/hardware_factory.hpp"

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

}  // namespace

void FrameAssembler::configure(const StationRuntimeConfig& config) {
  config_ = config;
  reset_devices();
}

bool FrameAssembler::ensure_initialized() {
  if (initialized_) {
    return true;
  }
  if (config_.lights.empty()) {
    return false;
  }

  auto light_controller = create_light_controller(config_.lights.front().backend);
  if (!light_controller->initialize(make_light_controller_config(config_.lights.front()))) {
    reset_devices();
    return false;
  }
  light_controllers_.push_back(std::move(light_controller));

  cameras_.clear();
  for (const auto& runtime_camera : config_.cameras) {
    CameraConfig camera_config;
    camera_config.camera_index = runtime_camera.camera_index;
    camera_config.camera_id = runtime_camera.camera_id;
    camera_config.serial_number = runtime_camera.serial_number;
    camera_config.calibration_id = runtime_camera.calibration_id;
    camera_config.width = runtime_camera.width;
    camera_config.height = runtime_camera.height;
    camera_config.channels = runtime_camera.channels;
    camera_config.pixel_format = runtime_camera.pixel_format;
    camera_config.trigger_line = runtime_camera.trigger_line;
    camera_config.exposure_output_line = runtime_camera.exposure_output_line;
    camera_config.buffer_count = runtime_camera.buffer_count;
    camera_config.simulate_missing_frame = runtime_camera.simulate_missing_frame;

    auto camera = create_camera(config_.camera_backend);
    if (!camera->initialize(camera_config)) {
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
  std::vector<RuntimeCaptureSlotConfig> capture_plan;
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
  if (!acquire_shared_light_parallel_frames(sequence, trigger, capture_plan, &frames, error)) {
    return false;
  }

  job.frame_count = static_cast<std::uint32_t>(frames.size());
  out_bundle->job_meta = job;
  out_bundle->frames = std::move(frames);
  if (!validate_shared_light_bundle(*out_bundle, sequence, capture_plan, error)) {
    reset_devices();
    return false;
  }
  return true;
}

bool FrameAssembler::prepare_light_sequence_for_view(const LightSequence& sequence,
                                                     std::uint64_t trigger_id,
                                                     const RuntimeCaptureSlotConfig& view,
                                                     AcquisitionError* error) {
  if (light_controllers_.size() != 1 || light_controllers_.front() == nullptr) {
    set_acquisition_error(error,
                          ErrorCode::LightFault,
                          AcquisitionStage::ConfigureLightSequence,
                          view.camera_index,
                          0,
                          0,
                          "FL-ACDH controller is not initialized");
    return false;
  }
  if (!light_controllers_.front()->prepare_sequence(
          sequence,
          trigger_id,
          config_.light_timeout_ms,
          error != nullptr ? &error->message : nullptr)) {
    const std::string detail = error != nullptr && !error->message.empty()
                                   ? error->message
                                   : "FL-ACDH light sequence prepare failed";
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
  return true;
}

bool FrameAssembler::acquire_shared_light_parallel_frames(
    const LightSequence& sequence,
    const ExternalTrigger& trigger,
    const std::vector<RuntimeCaptureSlotConfig>& capture_plan,
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
                          "shared light capture requires at least one view");
    return false;
  }
  if (!prepare_light_sequence_for_view(sequence, trigger.trigger_id, capture_plan.front(), error)) {
    return false;
  }

  std::vector<std::vector<CapturedFrame>> frames_by_view(
      capture_plan.size(), std::vector<CapturedFrame>(sequence.channels.size()));
  std::vector<std::vector<bool>> captured(
      capture_plan.size(), std::vector<bool>(sequence.channels.size(), false));

  for (std::uint32_t light_seq_index = 0;
       light_seq_index < sequence.channels.size();
       ++light_seq_index) {
    const auto light_param = sequence.channels[light_seq_index];
    if (!light_param.enabled) {
      continue;
    }

    // 所有相机并行 arm（各自通过独立 SDK handle 操作，无共享状态）。
    {
      std::vector<std::future<bool>> arm_futures;
      arm_futures.reserve(capture_plan.size());
      for (const auto& view : capture_plan) {
        arm_futures.push_back(std::async(std::launch::async, [&, &view = view]() {
          return arm_view_camera(trigger, view, light_param, light_seq_index, nullptr);
        }));
      }
      bool any_arm_failed = false;
      for (std::size_t vi = 0; vi < arm_futures.size(); ++vi) {
        if (!arm_futures[vi].get()) {
          any_arm_failed = true;
          record_camera_failure(capture_plan[vi].camera_index);
        } else {
          record_camera_success(capture_plan[vi].camera_index);
        }
      }
      if (any_arm_failed) {
        set_acquisition_error(error,
                              ErrorCode::CameraFault,
                              AcquisitionStage::ArmCamera,
                              capture_plan.front().camera_index,
                              light_param.light_index,
                              light_seq_index,
                              "one or more cameras failed to arm");
        handle_acquisition_failure();
        return false;
      }
    }
    // arm 完成后等待相机内部稳定（Exposure/Gain 应用完成），
    // 再向 FL-ACDH 发送触发命令，避免硬件触发沿被相机错过。
    if (config_.arm_settle_ms > 0) {
      std::this_thread::sleep_for(std::chrono::milliseconds(config_.arm_settle_ms));
    }
    // 对齐现场可工作的参考程序：先触发 FL-ACDH 产生硬触发帧，
    // 再调用 GetImageBuffer 读取 SDK 已缓存的帧。
    {
      struct FrameResult {
        bool ok = false;
        CapturedFrame frame;
        std::size_t view_index = 0;
        AcquisitionError error;
      };
      std::mutex wait_mutex;
      std::condition_variable wait_cv;
      std::uint32_t cameras_ready = 0;
      std::vector<std::future<bool>> drain_futures;
      drain_futures.reserve(capture_plan.size());
      for (std::size_t vi = 0; vi < capture_plan.size(); ++vi) {
        drain_futures.push_back(std::async(std::launch::async, [&, vi]() {
          auto* cam = camera_for_index(capture_plan[vi].camera_index);
          if (cam != nullptr) {
            cam->drain_stale_frames(100);
          }
          {
            std::lock_guard<std::mutex> lock(wait_mutex);
            ++cameras_ready;
          }
          wait_cv.notify_one();
          return cam != nullptr;
        }));
      }
      {
        std::unique_lock<std::mutex> lock(wait_mutex);
        wait_cv.wait(lock, [&]() {
          return cameras_ready == static_cast<std::uint32_t>(capture_plan.size());
        });
      }
      bool any_prepare_failed = false;
      for (auto& f : drain_futures) {
        if (!f.get()) {
          any_prepare_failed = true;
        }
      }
      if (any_prepare_failed) {
        set_acquisition_error(error,
                              ErrorCode::CameraFault,
                              AcquisitionStage::ArmCamera,
                              capture_plan.front().camera_index,
                              light_param.light_index,
                              light_seq_index,
                              "one or more cameras missing before light trigger");
        handle_acquisition_failure();
        return false;
      }
      std::this_thread::sleep_for(std::chrono::milliseconds(2));
      if (!light_controllers_.front()->trigger_channel(
              light_param,
              trigger.trigger_id,
              light_seq_index,
              config_.light_timeout_ms,
              error != nullptr ? &error->message : nullptr)) {
        const std::string detail = error != nullptr && !error->message.empty()
                                       ? error->message
                                       : "FL-ACDH channel trigger failed";
        set_acquisition_error(error,
                              ErrorCode::LightFault,
                              AcquisitionStage::TriggerLight,
                              capture_plan.front().camera_index,
                              light_param.light_index,
                              light_seq_index,
                              detail);
        handle_acquisition_failure();
        return false;
      }
      std::vector<std::future<FrameResult>> frame_futures;
      frame_futures.reserve(capture_plan.size());
      for (std::size_t vi = 0; vi < capture_plan.size(); ++vi) {
        frame_futures.push_back(std::async(std::launch::async, [&, vi]() -> FrameResult {
          FrameResult result;
          result.view_index = vi;
          result.ok = wait_view_light_frame(trigger, capture_plan[vi], light_param,
                                            light_seq_index, &result.frame, &result.error);
          return result;
        }));
      }
      bool any_wait_failed = false;
      std::ostringstream wait_failure_detail;
      for (auto& f : frame_futures) {
        auto result = f.get();
        if (!result.ok) {
          any_wait_failed = true;
          record_camera_failure(capture_plan[result.view_index].camera_index);
          if (wait_failure_detail.tellp() > 0) {
            wait_failure_detail << " | ";
          }
          wait_failure_detail << result.error.message;
          continue;
        }
        record_camera_success(capture_plan[result.view_index].camera_index);
        frames_by_view[result.view_index][light_seq_index] = std::move(result.frame);
        captured[result.view_index][light_seq_index] = true;
      }
      if (any_wait_failed) {
        set_acquisition_error(error,
                              ErrorCode::MissingFrame,
                              AcquisitionStage::WaitFrame,
                              capture_plan.front().camera_index,
                              light_param.light_index,
                              light_seq_index,
                              wait_failure_detail.tellp() > 0
                                  ? wait_failure_detail.str()
                                  : "one or more cameras timed out waiting for frame");
        handle_acquisition_failure();
        return false;
      }
    }
  }

  // 物理上按光源同步采集，发布顺序仍保持视角优先，方便 Python 按 camera/pose 分组。
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
                              "shared light capture missed an expected frame");
        handle_acquisition_failure();
        return false;
      }
      frames->push_back(std::move(frames_by_view[view_index][light_seq_index]));
    }
  }
  consecutive_failures_ = 0;
  return true;
}

bool FrameAssembler::arm_view_camera(const ExternalTrigger& trigger,
                                     const RuntimeCaptureSlotConfig& view,
                                     const LightChannelParam& light_param,
                                     std::uint32_t light_seq_index,
                                     AcquisitionError* error) {
  auto* camera_ptr = camera_for_index(view.camera_index);
  if (camera_ptr == nullptr) {
    std::ostringstream oss;
    oss << "capture view references missing camera_index=" << view.camera_index
        << " view_id=" << view.view_id;
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
                                           const RuntimeCaptureSlotConfig& view,
                                           const LightChannelParam& light_param,
                                           std::uint32_t light_seq_index,
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
    const auto health = camera_ptr->get_health();
    std::string serial_number;
    std::string trigger_line;
    for (const auto& camera : config_.cameras) {
      if (camera.camera_index == view.camera_index) {
        serial_number = camera.serial_number;
        trigger_line = camera.trigger_line;
        break;
      }
    }
    std::ostringstream oss;
    oss << "camera frame timeout view_id=" << view.view_id
        << " camera_index=" << view.camera_index
        << " camera_id=" << view.camera_id
        << " serial_number=" << serial_number
        << " trigger_line=" << trigger_line
        << " light_index=" << light_param.light_index
        << " light_seq_index=" << light_seq_index
        << " timeout_ms=" << config_.camera_timeout_ms
        << " camera_health_ok=" << (health.ok ? "true" : "false")
        << " dropped_frames=" << health.dropped_frames
        << " camera_message=" << health.message;
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
  out_frame->meta.view_index = view.view_index;
  out_frame->meta.shot_id = trigger.trigger_id;
  out_frame->meta.reserved_u64 = 0;
  for (float& value : out_frame->meta.reserved_f32) {
    value = 0.0F;
  }
  copy_cstr(out_frame->meta.camera_id, view.camera_id);
  copy_cstr(out_frame->meta.view_id, view.view_id);
  copy_cstr(out_frame->meta.calibration_id, view.calibration_id);
  return true;
}

void FrameAssembler::handle_acquisition_failure() {
  ++consecutive_failures_;
  if (consecutive_failures_ >= config_.max_camera_failures_before_reset) {
    std::cerr << "frame_assembler: " << consecutive_failures_
              << " consecutive acquisition failures, resetting all devices" << std::endl;
    reset_devices();
    consecutive_failures_ = 0;
  }
}

void FrameAssembler::record_camera_success(std::uint32_t camera_index) {
  camera_failures_[camera_index] = 0;
}

void FrameAssembler::record_camera_failure(std::uint32_t camera_index) {
  ++camera_failures_[camera_index];
  if (camera_failures_[camera_index] >= config_.max_camera_failures_before_reset) {
    std::cerr << "frame_assembler: camera_index=" << camera_index
              << " failed " << camera_failures_[camera_index]
              << " consecutive times, resetting camera" << std::endl;
    reset_camera(camera_index);
    camera_failures_[camera_index] = 0;
  }
}

void FrameAssembler::reset_camera(std::uint32_t camera_index) {
  auto* cam = camera_for_index(camera_index);
  if (cam != nullptr) {
    cam->stop();
    cam->start();
    std::cerr << "frame_assembler: camera_index=" << camera_index
              << " stop+start cycle complete" << std::endl;
  }
}

void FrameAssembler::reset_devices() {
  for (auto& ctrl : light_controllers_) {
    if (ctrl) {
      ctrl->shutdown_all();
    }
  }
  light_controllers_.clear();
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
    if (configured.controller_index != 0 ||
        configured.acquisition_mode != LightAcquisitionMode::Strobe ||
        configured.physical_channel == 0 ||
        configured.exposure_us == 0 ||
        configured.strobe_width_us == 0 ||
        configured.strobe_width_us > configured.exposure_us ||
        configured.gain <= 0.0F ||
        configured.current_percent <= 0.0F ||
        configured.current_percent > 100.0F) {
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
    param.controller_index = 0;
    param.light_index = configured.light_index;
    param.physical_channel = configured.physical_channel;
    param.exposure_us = configured.exposure_us;
    param.strobe_width_us = configured.strobe_width_us;
    param.trigger_delay_us = configured.trigger_delay_us;
    param.gain = configured.gain;
    param.current_percent = configured.current_percent;
    param.acquisition_mode = LightAcquisitionMode::Strobe;
    out_sequence->channels.push_back(param);
  }
  return true;
}

bool FrameAssembler::validate_shared_light_bundle(const SeatImageBundle& bundle,
                                                  const LightSequence& sequence,
                                                  const std::vector<RuntimeCaptureSlotConfig>& views,
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
                          "bundle frame_count mismatch");
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
          meta.view_index != view.view_index ||
          meta.light_index != expected_light.light_index ||
          meta.light_seq_index != light_seq_index) {
        std::ostringstream oss;
        oss << "bundle order mismatch expected view_index=" << view.view_index
            << " camera_index=" << view.camera_index
            << " light_index=" << expected_light.light_index
            << " light_seq_index=" << light_seq_index
            << " actual view_index=" << meta.view_index
            << " actual camera_index=" << meta.camera_index
            << " actual light_index=" << meta.light_index
            << " actual light_seq_index=" << meta.light_seq_index;
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
        oss << "frame metadata invalid camera_index=" << camera.camera_index
            << " light_index=" << expected_light.light_index;
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
        oss << "frame payload too small camera_index=" << camera.camera_index
            << " light_index=" << expected_light.light_index;
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

bool FrameAssembler::build_capture_plan(std::vector<RuntimeCaptureSlotConfig>* out_views,
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
  if (config_.cameras.empty()) {
    set_acquisition_error(error,
                          ErrorCode::ConfigurationError,
                          AcquisitionStage::Configuration,
                          0,
                          0,
                          0,
                          "fixed station capture requires at least one camera");
    return false;
  }
  for (const auto& camera : config_.cameras) {
    RuntimeCaptureSlotConfig view;
    view.view_index = camera.camera_index;
    view.view_id = camera.camera_id;
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

}  // namespace seat_aoi
