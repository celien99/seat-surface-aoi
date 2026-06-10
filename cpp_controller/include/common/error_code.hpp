#pragma once

#include <cstdint>

namespace seat_aoi {

enum class ErrorCode : std::uint32_t {
  None = 0,
  ProtocolMismatch = 1,
  InvalidPayload = 2,
  CrcMismatch = 3,
  SlotUnavailable = 4,
  DetectorTimeout = 5,
  MissingFrame = 6,
  QualityFailed = 7,
  DeviceFault = 8,
  InternalError = 9,
  LightFault = 10,
  CameraFault = 11,
  TriggerSyncFault = 12,
  ConfigurationError = 13,
};

}  // namespace seat_aoi
