#pragma once

#include <memory>

#include "camera/camera_worker.hpp"
#include "control/light_controller.hpp"
#include "control/plc_client.hpp"

namespace seat_aoi {

enum class HardwareBackend {
  Simulated,
  // RealModbus,      // Phase 2
  // RealBasler,      // Phase 4
  // RealSerialLight, // Phase 3
};

inline std::unique_ptr<IPlcClient> create_plc_client(HardwareBackend /*backend*/) {
  return std::make_unique<SimPlcClient>();
}

inline std::unique_ptr<ILightController> create_light_controller(HardwareBackend /*backend*/) {
  return std::make_unique<SimLightController>();
}

inline std::unique_ptr<ICamera> create_camera(HardwareBackend /*backend*/) {
  return std::make_unique<SimCamera>();
}

}  // namespace seat_aoi
