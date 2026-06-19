cmake_minimum_required(VERSION 3.16)

project(seat_surface_aoi_controller LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

if(MSVC)
  add_compile_options(/utf-8)
endif()

option(SEAT_AOI_ENABLE_HIKROBOT_MVS "Enable Hikrobot MVS camera SDK backend" OFF)
set(SEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR "" CACHE PATH "Hikrobot MVS SDK include directory")
set(SEAT_AOI_HIKROBOT_MVS_LIBRARY "" CACHE FILEPATH "Hikrobot MVS SDK library")

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
  src/control/fl_acdh_light_controller.cpp
  src/control/light_controller.cpp
  src/control/signal_client.cpp
  src/control/tcp_signal_client.cpp
  src/control/robot_client.cpp
  src/control/production_event_log.cpp
  src/control/station_health.cpp
  src/control/station_runtime_config.cpp
  src/camera/camera_device.cpp
  src/camera/hikrobot_mvs_camera.cpp
  src/camera/camera_worker.cpp
  src/control/trigger_scheduler.cpp
  src/control/frame_assembler.cpp
  src/control/station_controller.cpp
  src/control/image_writer.cpp
  src/control/distance_sensor.cpp
  src/control/distance_trigger_signal_client.cpp
)

target_include_directories(seat_aoi_control PUBLIC include)
target_link_libraries(seat_aoi_control PUBLIC seat_aoi_ipc)
if(SEAT_AOI_ENABLE_HIKROBOT_MVS)
  if(NOT SEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR)
    find_path(SEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR
      NAMES MvCameraControl.h
      HINTS
        "C:/Program Files (x86)/MVS/Development/Includes"
        "C:/Program Files/MVS/Development/Includes"
        "/opt/MVS/include"
    )
  endif()
  if(NOT SEAT_AOI_HIKROBOT_MVS_LIBRARY)
    find_library(SEAT_AOI_HIKROBOT_MVS_LIBRARY
      NAMES MvCameraControl
      HINTS
        "C:/Program Files (x86)/MVS/Development/Libraries/win64"
        "C:/Program Files/MVS/Development/Libraries/win64"
        "/opt/MVS/lib/64"
        "/opt/MVS/lib"
    )
  endif()
  if(NOT SEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR OR NOT SEAT_AOI_HIKROBOT_MVS_LIBRARY)
    message(FATAL_ERROR "启用 SEAT_AOI_ENABLE_HIKROBOT_MVS 时必须提供 MVS include/lib 路径")
  endif()
  target_compile_definitions(seat_aoi_control PUBLIC SEAT_AOI_ENABLE_HIKROBOT_MVS)
  target_include_directories(seat_aoi_control PUBLIC "${SEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR}")
  target_link_libraries(seat_aoi_control PUBLIC "${SEAT_AOI_HIKROBOT_MVS_LIBRARY}")
endif()
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
