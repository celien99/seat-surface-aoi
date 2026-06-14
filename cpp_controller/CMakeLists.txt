cmake_minimum_required(VERSION 3.16)

project(seat_surface_aoi_controller LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

set(SEAT_AOI_SHARED_MEMORY_SOURCE src/ipc/shared_memory_posix.cpp)
if(WIN32)
  set(SEAT_AOI_SHARED_MEMORY_SOURCE src/ipc/shared_memory_win32.cpp)
endif()

add_library(seat_aoi_ipc
  src/ipc/crc32.cpp
  ${SEAT_AOI_SHARED_MEMORY_SOURCE}
  src/ipc/frame_ring_buffer.cpp
  src/ipc/result_ring_buffer.cpp
)

target_include_directories(seat_aoi_ipc PUBLIC include)

if(WIN32)
  target_compile_definitions(seat_aoi_ipc PUBLIC NOMINMAX WIN32_LEAN_AND_MEAN)
elseif(UNIX AND NOT APPLE)
  target_link_libraries(seat_aoi_ipc PUBLIC rt pthread)
else()
  target_link_libraries(seat_aoi_ipc PUBLIC pthread)
endif()

add_library(seat_aoi_control
  src/control/hardware_backend.cpp
  src/control/light_controller.cpp
  src/control/plc_client.cpp
  src/control/robot_client.cpp
  src/control/production_event_log.cpp
  src/control/station_health.cpp
  src/control/station_runtime_config.cpp
  src/camera/camera_device.cpp
  src/camera/camera_worker.cpp
  src/control/trigger_scheduler.cpp
  src/control/frame_assembler.cpp
  src/control/station_controller.cpp
)

target_include_directories(seat_aoi_control PUBLIC include)
target_link_libraries(seat_aoi_control PUBLIC seat_aoi_ipc)
if(CMAKE_CXX_COMPILER_ID STREQUAL "GNU" AND CMAKE_CXX_COMPILER_VERSION VERSION_LESS 9)
  target_link_libraries(seat_aoi_control PUBLIC stdc++fs)
endif()

add_executable(seat_aoi_controller src/main.cpp)
target_include_directories(seat_aoi_controller PRIVATE include)
target_link_libraries(seat_aoi_controller PRIVATE seat_aoi_control)

add_executable(protocol_layout tools/protocol_layout.cpp)
target_include_directories(protocol_layout PRIVATE include)

add_executable(ipc_safety_checks tools/ipc_safety_checks.cpp)
target_link_libraries(ipc_safety_checks PRIVATE seat_aoi_control)
