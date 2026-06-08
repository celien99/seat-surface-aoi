#pragma once

#include <cstdint>
#include <string>
#include <vector>

#include "camera/camera_worker.hpp"
#include "control/light_controller.hpp"
#include "control/trigger_scheduler.hpp"
#include "ipc/frame_ring_buffer.hpp"

namespace seat_aoi {

struct Recipe {
  std::string recipe_id = "seat_a_black_leather_v1";
  std::vector<std::uint32_t> light_order = {1, 2, 3, 4};
};

class FrameAssembler {
public:
  bool acquire_bundles(const Recipe& recipe,
                       const PlcTrigger& trigger,
                       std::uint64_t sequence_id,
                       SeatImageBundle* out_bundle,
                       std::string* error_message);

private:
  bool ensure_initialized();

  bool initialized_ = false;
  LightController light_controller_;
  std::vector<CameraWorker> cameras_;
};

}  // namespace seat_aoi

