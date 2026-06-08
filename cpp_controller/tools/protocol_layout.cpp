#include <iostream>

#include "common/inspection_types.hpp"
#include "ipc/shm_protocol.hpp"

int main() {
  std::cout << "ShmHeader=" << sizeof(seat_aoi::ShmHeader) << "\n";
  std::cout << "FrameSlotHeader=" << sizeof(seat_aoi::FrameSlotHeader) << "\n";
  std::cout << "ResultSlotHeader=" << sizeof(seat_aoi::ResultSlotHeader) << "\n";
  std::cout << "LightFrameMeta=" << sizeof(seat_aoi::LightFrameMeta) << "\n";
  std::cout << "SeatJobMeta=" << sizeof(seat_aoi::SeatJobMeta) << "\n";
  std::cout << "InspectionResultMeta=" << sizeof(seat_aoi::InspectionResultMeta) << "\n";
  std::cout << "DefectResultMeta=" << sizeof(seat_aoi::DefectResultMeta) << "\n";
  return 0;
}

