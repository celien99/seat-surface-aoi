#include "control/frame_assembler.hpp"

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
    light_controller_ = create_light_controller(HardwareBackend::Simulated);
  }
  if (!light_controller_->initialize(config_.light.simulate_fault)) {
    return false;
  }
  cameras_.clear();
  for (const auto& runtime_camera : config_.cameras) {
    CameraConfig config;
    config.camera_index = runtime_camera.camera_index;
    config.camera_id = runtime_camera.camera_id;
    config.width = runtime_camera.width;
    config.height = runtime_camera.height;
    config.channels = runtime_camera.channels;
    config.simulate_missing_frame = runtime_camera.simulate_missing_frame;
    auto camera = create_camera(HardwareBackend::Simulated);
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
                                     std::string* error_message) {
  if (!ensure_initialized()) {
    if (error_message != nullptr) {
      *error_message = "failed to initialize simulated acquisition";
    }
    return false;
  }
  if (out_bundle == nullptr) {
    return false;
  }

  // Build light sequence from recipe (one strobe per channel)
  LightSequence sequence;
  for (std::uint32_t light_index : recipe.light_order) {
    sequence.channels.push_back(LightChannelParam{light_index, 800, 1.0F, 60.0F});
  }

  SeatJobMeta job{};
  job.sequence_id = sequence_id;
  job.trigger_id = trigger.trigger_id;
  copy_cstr(job.seat_id, trigger.seat_id);
  copy_cstr(job.sku, trigger.sku);
  copy_cstr(job.recipe_id, recipe.recipe_id);
  job.camera_count = static_cast<std::uint32_t>(cameras_.size());
  job.created_at_us = now_us();

  std::vector<CapturedFrame> frames;
  frames.reserve(cameras_.size() * sequence.channels.size());

  // 时分频闪方案：外层按机位串行，内层按光源串行
  // 每个机位独立完成全部光源频闪序列后，下一个机位才开始
  for (std::uint32_t camera_index = 0; camera_index < cameras_.size(); ++camera_index) {
    auto& camera = *cameras_[camera_index];

    // 每个机位开始前重新准备光源序列
    if (!light_controller_->prepare_sequence(sequence,
                                            trigger.trigger_id,
                                            config_.light_timeout_ms,
                                            error_message)) {
      if (error_message != nullptr && error_message->empty()) {
        std::ostringstream oss;
        oss << "simulated light sequence prepare failed camera_index=" << camera_index;
        *error_message = oss.str();
      }
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
                                               error_message)) {
          if (error_message != nullptr && error_message->empty()) {
            *error_message = "simulated light channel failed";
          }
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
                                                    error_message)) {
          if (error_message != nullptr && error_message->empty()) {
            *error_message = "simulated light hardware trigger arm failed";
          }
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
          if (error_message != nullptr) {
            std::ostringstream oss;
            oss << "simulated camera arm failed camera_index=" << camera_index
                << " light_index=" << light_param.light_index
                << " light_seq_index=" << light_seq_index;
            *error_message = oss.str();
          }
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
          if (error_message != nullptr) {
            std::ostringstream oss;
            oss << "simulated camera exposure output failed camera_index=" << camera_index
                << " light_index=" << light_param.light_index
                << " light_seq_index=" << light_seq_index;
            *error_message = oss.str();
          }
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
                                                         error_message)) {
          if (error_message != nullptr && error_message->empty()) {
            *error_message = "simulated light hardware trigger failed";
          }
          light_controller_->shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }
      } else {
        if (error_message != nullptr) {
          *error_message = std::string("unsupported trigger sync mode ") +
                           trigger_sync_mode_name(config_.trigger_sync_mode);
        }
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
        if (error_message != nullptr) {
          std::ostringstream oss;
          oss << "simulated camera frame timeout camera_index=" << camera_index
              << " light_index=" << light_param.light_index
              << " light_seq_index=" << light_seq_index;
          *error_message = oss.str();
        }
        light_controller_->shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }
      frames.push_back(std::move(frame));
    }
  }

  job.frame_count = static_cast<std::uint32_t>(frames.size());
  out_bundle->job_meta = job;
  out_bundle->frames = std::move(frames);
  return true;
}

}  // namespace seat_aoi
