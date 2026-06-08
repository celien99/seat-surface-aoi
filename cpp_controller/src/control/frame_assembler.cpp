#include "control/frame_assembler.hpp"

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"

namespace seat_aoi {

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
  if (!light_controller_.run_sequence(sequence, trigger.trigger_id, config_.light_timeout_ms)) {
    if (error_message != nullptr) {
      *error_message = "simulated light sequence failed";
    }
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
  for (std::uint32_t camera_index = 0; camera_index < cameras_.size(); ++camera_index) {
    for (std::uint32_t light_seq_index = 0; light_seq_index < recipe.light_order.size();
         ++light_seq_index) {
      CapturedFrame frame;
      if (!cameras_[camera_index].wait_frame(trigger.trigger_id,
                                             recipe.light_order[light_seq_index],
                                             light_seq_index,
                                             &frame,
                                             config_.camera_timeout_ms)) {
        if (error_message != nullptr) {
          *error_message = "simulated camera frame timeout";
        }
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
