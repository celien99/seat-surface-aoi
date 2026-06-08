#include "control/frame_assembler.hpp"

#include <future>
#include <sstream>

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
  if (!light_controller_.initialize(config_.light.simulate_fault)) {
    return false;
  }
  cameras_.clear();
  for (const auto& runtime_camera : config_.cameras) {
    CameraWorker worker;
    CameraConfig config;
    config.camera_index = runtime_camera.camera_index;
    config.camera_id = runtime_camera.camera_id;
    config.width = runtime_camera.width;
    config.height = runtime_camera.height;
    config.channels = runtime_camera.channels;
    config.simulate_missing_frame = runtime_camera.simulate_missing_frame;
    if (!worker.initialize(config)) {
      return false;
    }
    worker.start();
    cameras_.push_back(std::move(worker));
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

  LightSequence sequence;
  for (std::uint32_t light_index : recipe.light_order) {
    sequence.channels.push_back(LightChannelParam{light_index, 800, 1.0F, 60.0F});
  }
  if (!light_controller_.prepare_sequence(sequence,
                                          trigger.trigger_id,
                                          config_.light_timeout_ms,
                                          error_message)) {
    if (error_message != nullptr) {
      if (error_message->empty()) {
        *error_message = "simulated light sequence prepare failed";
      }
    }
    light_controller_.shutdown_all();
    initialized_ = false;
    cameras_.clear();
    return false;
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
  for (std::uint32_t light_seq_index = 0; light_seq_index < sequence.channels.size();
       ++light_seq_index) {
    const auto light_param = sequence.channels[light_seq_index];
    if (config_.trigger_sync_mode == TriggerSyncMode::Software) {
      if (!light_controller_.trigger_channel(light_param,
                                             trigger.trigger_id,
                                             light_seq_index,
                                             config_.light_timeout_ms,
                                             error_message)) {
        if (error_message != nullptr && error_message->empty()) {
          *error_message = "simulated light channel failed";
        }
        light_controller_.shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }
    } else if (config_.trigger_sync_mode == TriggerSyncMode::CameraExposureOutput) {
      if (!light_controller_.arm_hardware_trigger(light_param,
                                                  trigger.trigger_id,
                                                  light_seq_index,
                                                  config_.light_timeout_ms,
                                                  error_message)) {
        if (error_message != nullptr && error_message->empty()) {
          *error_message = "simulated light hardware trigger arm failed";
        }
        light_controller_.shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }
      for (std::uint32_t camera_index = 0; camera_index < cameras_.size(); ++camera_index) {
        if (!cameras_[camera_index].arm(trigger.trigger_id,
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
          light_controller_.shutdown_all();
          initialized_ = false;
          cameras_.clear();
          return false;
        }
      }
      if (!cameras_.empty() &&
          !cameras_[0].simulate_exposure_output(trigger.trigger_id,
                                                light_param,
                                                light_seq_index,
                                                config_.camera_timeout_ms)) {
        if (error_message != nullptr) {
          std::ostringstream oss;
          oss << "simulated camera exposure output failed camera_index=0"
              << " light_index=" << light_param.light_index
              << " light_seq_index=" << light_seq_index;
          *error_message = oss.str();
        }
        light_controller_.shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }
      if (!light_controller_.notify_hardware_triggered(light_param,
                                                       trigger.trigger_id,
                                                       light_seq_index,
                                                       config_.light_timeout_ms,
                                                       error_message)) {
        if (error_message != nullptr && error_message->empty()) {
          *error_message = "simulated light hardware trigger failed";
        }
        light_controller_.shutdown_all();
        initialized_ = false;
        cameras_.clear();
        return false;
      }
    } else {
      if (error_message != nullptr) {
        *error_message = std::string("unsupported trigger sync mode ") +
                         trigger_sync_mode_name(config_.trigger_sync_mode);
      }
      light_controller_.shutdown_all();
      initialized_ = false;
      cameras_.clear();
      return false;
    }

    std::vector<std::future<CapturedFrame>> futures;
    futures.reserve(cameras_.size());
    for (std::uint32_t camera_index = 0; camera_index < cameras_.size(); ++camera_index) {
      futures.push_back(std::async(std::launch::async,
                                   [this, camera_index, trigger_id = trigger.trigger_id,
                                    light_param, light_seq_index]() {
                                     CapturedFrame frame;
                                     if (!cameras_[camera_index].wait_frame(trigger_id,
                                                                            light_param,
                                                                            light_seq_index,
                                                                            &frame,
                                                                            config_.camera_timeout_ms)) {
                                       frame.bytes.clear();
                                     }
                                     return frame;
                                   }));
    }

    for (std::uint32_t camera_index = 0; camera_index < futures.size(); ++camera_index) {
      CapturedFrame frame = futures[camera_index].get();
      if (frame.bytes.empty()) {
        if (error_message != nullptr) {
          std::ostringstream oss;
          oss << "simulated camera frame timeout camera_index=" << camera_index
              << " light_index=" << light_param.light_index
              << " light_seq_index=" << light_seq_index;
          *error_message = oss.str();
        }
        light_controller_.shutdown_all();
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
