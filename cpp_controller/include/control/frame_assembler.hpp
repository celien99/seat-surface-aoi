#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "camera/icamera.hpp"
#include "common/error_code.hpp"
#include "control/external_trigger.hpp"
#include "control/ilight_controller.hpp"
#include "control/irobot_client.hpp"
#include "control/station_runtime_config.hpp"
#include "ipc/frame_ring_buffer.hpp"

namespace seat_aoi {

struct Recipe {
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
};

enum class AcquisitionStage : std::uint32_t {
  None = 0,
  Initialize = 1,
  ConfigureLightSequence = 2,
  TriggerLight = 3,
  ArmLight = 4,
  ArmCamera = 5,
  ExposureOutput = 6,
  ConfirmLightTrigger = 7,
  WaitFrame = 8,
  Configuration = 9,
};

struct AcquisitionError {
  ErrorCode code = ErrorCode::None;
  AcquisitionStage stage = AcquisitionStage::None;
  std::uint32_t camera_index = 0;
  std::uint32_t light_index = 0;
  std::uint32_t light_seq_index = 0;
  std::string message;
};

class FrameAssembler {
public:
  void configure(const StationRuntimeConfig& config);
  bool acquire_bundles(const Recipe& recipe,
                       const ExternalTrigger& trigger,
                       std::uint64_t sequence_id,
                       SeatImageBundle* out_bundle,
                       AcquisitionError* error);

private:
  bool ensure_initialized();
  bool build_light_sequence(const Recipe& recipe,
                            LightSequence* out_sequence,
                            AcquisitionError* error) const;
  bool build_capture_plan(std::vector<RuntimeCaptureViewConfig>* out_views,
                          AcquisitionError* error) const;
  bool validate_serial_tdm_bundle(const SeatImageBundle& bundle,
                                  const LightSequence& sequence,
                                  const std::vector<RuntimeCaptureViewConfig>& views,
                                  AcquisitionError* error) const;
  bool prepare_light_sequence_for_view(const LightSequence& sequence,
                                       std::uint64_t trigger_id,
                                       const RuntimeCaptureViewConfig& view,
                                       AcquisitionError* error);
  bool acquire_view_serial_tdm_frames(const LightSequence& sequence,
                                      const ExternalTrigger& trigger,
                                      const std::vector<RuntimeCaptureViewConfig>& capture_plan,
                                      std::vector<CapturedFrame>* frames,
                                      AcquisitionError* error);
  bool acquire_shared_light_parallel_frames(const LightSequence& sequence,
                                            const ExternalTrigger& trigger,
                                            const std::vector<RuntimeCaptureViewConfig>& capture_plan,
                                            std::vector<CapturedFrame>* frames,
                                            AcquisitionError* error);
  bool arm_view_camera(const ExternalTrigger& trigger,
                       const RuntimeCaptureViewConfig& view,
                       const LightChannelParam& light_param,
                       std::uint32_t light_seq_index,
                       AcquisitionError* error);
  bool wait_view_light_frame(const ExternalTrigger& trigger,
                             const RuntimeCaptureViewConfig& view,
                             const LightChannelParam& light_param,
                             std::uint32_t light_seq_index,
                             const RobotPoseStatus& pose_status,
                             CapturedFrame* out_frame,
                             AcquisitionError* error);
  void reset_devices();
  ICamera* camera_for_index(std::uint32_t camera_index) const;
  bool wait_robot_pose_ready(const ExternalTrigger& trigger,
                             const RuntimeCaptureViewConfig& view,
                             RobotPoseStatus* out_status,
                             AcquisitionError* error);

  bool initialized_ = false;
  StationRuntimeConfig config_{};
  std::vector<std::unique_ptr<ILightController>> light_controllers_;
  std::unique_ptr<IRobotClient> robot_client_;
  std::vector<std::unique_ptr<ICamera>> cameras_;
};

}  // namespace seat_aoi
