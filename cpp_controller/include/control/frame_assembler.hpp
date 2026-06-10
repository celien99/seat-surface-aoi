#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "camera/icamera.hpp"
#include "common/error_code.hpp"
#include "control/ilight_controller.hpp"
#include "control/station_runtime_config.hpp"
#include "control/trigger_scheduler.hpp"
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
                       const PlcTrigger& trigger,
                       std::uint64_t sequence_id,
                       SeatImageBundle* out_bundle,
                       AcquisitionError* error);

private:
  bool ensure_initialized();
  bool build_light_sequence(const Recipe& recipe,
                            LightSequence* out_sequence,
                            AcquisitionError* error) const;

  bool initialized_ = false;
  StationRuntimeConfig config_{};
  std::unique_ptr<ILightController> light_controller_;
  std::vector<std::unique_ptr<ICamera>> cameras_;
};

}  // namespace seat_aoi
