#pragma once

#include <cstdint>
#include <memory>
#include <string>
#include <vector>

#include "camera/camera_worker.hpp"
#include "control/ilight_controller.hpp"
#include "control/station_runtime_config.hpp"
#include "control/trigger_scheduler.hpp"
#include "ipc/frame_ring_buffer.hpp"

namespace seat_aoi {

struct Recipe {
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
};

class FrameAssembler {
public:
  void configure(const StationRuntimeConfig& config);
  bool acquire_bundles(const Recipe& recipe,
                       const PlcTrigger& trigger,
                       std::uint64_t sequence_id,
                       SeatImageBundle* out_bundle,
                       std::string* error_message);

private:
  bool ensure_initialized();

  bool initialized_ = false;
  StationRuntimeConfig config_{};
  std::unique_ptr<ILightController> light_controller_;
  std::vector<CameraWorker> cameras_;
};

}  // namespace seat_aoi
