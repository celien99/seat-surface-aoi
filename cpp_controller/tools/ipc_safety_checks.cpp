#include <array>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <thread>
#include <vector>

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"
#include "control/fl_acdh_light_controller.hpp"
#include "control/image_writer.hpp"
#include "control/hardware_factory.hpp"
#include "control/signal_client.hpp"
#include "control/station_health.hpp"
#include "control/station_controller.hpp"
#include "control/station_runtime_config.hpp"
#include "ipc/crc32.hpp"
#include "ipc/frame_ring_buffer.hpp"
#include "ipc/result_ring_buffer.hpp"
#include "ipc/shared_memory.hpp"

namespace {

seat_aoi::StationRuntimeConfig make_filled_production_runtime_config();

#ifdef _WIN32
using TestSocket = SOCKET;
constexpr TestSocket kInvalidTestSocket = INVALID_SOCKET;

bool ensure_test_winsock(std::string* error_message) {
  static bool initialized = false;
  if (initialized) {
    return true;
  }
  WSADATA data{};
  const int ret = WSAStartup(MAKEWORD(2, 2), &data);
  if (ret != 0) {
    if (error_message != nullptr) {
      *error_message = "test WSAStartup failed: " + std::to_string(ret);
    }
    return false;
  }
  initialized = true;
  return true;
}

void close_test_socket(TestSocket sock) {
  if (sock != kInvalidTestSocket) {
    closesocket(sock);
  }
}
#else
using TestSocket = int;
constexpr TestSocket kInvalidTestSocket = -1;

bool ensure_test_winsock(std::string* /*error_message*/) {
  return true;
}

void close_test_socket(TestSocket sock) {
  if (sock != kInvalidTestSocket) {
    close(sock);
  }
}
#endif

bool set_test_socket_timeout(TestSocket sock, int timeout_ms, std::string* error_message) {
#ifdef _WIN32
  const DWORD tv = static_cast<DWORD>(timeout_ms);
  const int recv_ret = setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO,
                                  reinterpret_cast<const char*>(&tv), sizeof(tv));
  const int send_ret = setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO,
                                  reinterpret_cast<const char*>(&tv), sizeof(tv));
#else
  timeval tv{};
  tv.tv_sec = timeout_ms / 1000;
  tv.tv_usec = (timeout_ms % 1000) * 1000;
  const int recv_ret = setsockopt(sock, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  const int send_ret = setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
#endif
  if (recv_ret != 0 || send_ret != 0) {
    if (error_message != nullptr) {
      *error_message = "test socket timeout setup failed";
    }
    return false;
  }
  return true;
}

std::uint16_t reserve_tcp_test_port(std::string* error_message) {
  if (!ensure_test_winsock(error_message)) {
    return 0;
  }
  TestSocket sock =
#ifdef _WIN32
      socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
#else
      socket(AF_INET, SOCK_STREAM, 0);
#endif
  if (sock == kInvalidTestSocket) {
    if (error_message != nullptr) {
      *error_message = "test socket create failed";
    }
    return 0;
  }

  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
  addr.sin_port = 0;
  if (bind(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test socket bind failed";
    }
    return 0;
  }

#ifdef _WIN32
  int addr_len = sizeof(addr);
#else
  socklen_t addr_len = sizeof(addr);
#endif
  if (getsockname(sock, reinterpret_cast<sockaddr*>(&addr), &addr_len) != 0) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test socket getsockname failed";
    }
    return 0;
  }
  const auto port = static_cast<std::uint16_t>(ntohs(addr.sin_port));
  close_test_socket(sock);
  return port;
}

bool send_tcp_test_payload(std::uint16_t port,
                           const std::string& payload,
                           std::string* response,
                           std::string* error_message) {
  if (!ensure_test_winsock(error_message)) {
    return false;
  }
  TestSocket sock =
#ifdef _WIN32
      socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
#else
      socket(AF_INET, SOCK_STREAM, 0);
#endif
  if (sock == kInvalidTestSocket) {
    if (error_message != nullptr) {
      *error_message = "test client socket create failed";
    }
    return false;
  }
  if (!set_test_socket_timeout(sock, 1000, error_message)) {
    close_test_socket(sock);
    return false;
  }

  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(port);
  if (inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr) != 1) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test client loopback parse failed";
    }
    return false;
  }
  if (connect(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test client connect failed";
    }
    return false;
  }

  const int sent = send(sock, payload.data(), static_cast<int>(payload.size()), 0);
  if (sent != static_cast<int>(payload.size())) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test client send failed";
    }
    return false;
  }

  char buffer[64]{};
  const int received = recv(sock, buffer, static_cast<int>(sizeof(buffer)), 0);
  close_test_socket(sock);
  if (received <= 0) {
    if (error_message != nullptr) {
      *error_message = "test client did not receive ack";
    }
    return false;
  }
  if (response != nullptr) {
    response->assign(buffer, buffer + received);
  }
  return true;
}

bool send_tcp_test_payload_chunks(std::uint16_t port,
                                  const std::vector<std::string>& payloads,
                                  int gap_ms,
                                  std::string* response,
                                  std::string* error_message) {
  if (!ensure_test_winsock(error_message)) {
    return false;
  }
  TestSocket sock =
#ifdef _WIN32
      socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
#else
      socket(AF_INET, SOCK_STREAM, 0);
#endif
  if (sock == kInvalidTestSocket) {
    if (error_message != nullptr) {
      *error_message = "test client socket create failed";
    }
    return false;
  }
  if (!set_test_socket_timeout(sock, 1000, error_message)) {
    close_test_socket(sock);
    return false;
  }

  sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(port);
  if (inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr) != 1) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test client loopback parse failed";
    }
    return false;
  }
  if (connect(sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
    close_test_socket(sock);
    if (error_message != nullptr) {
      *error_message = "test client connect failed";
    }
    return false;
  }

  for (std::size_t index = 0; index < payloads.size(); ++index) {
    const auto& payload = payloads[index];
    const int sent = send(sock, payload.data(), static_cast<int>(payload.size()), 0);
    if (sent != static_cast<int>(payload.size())) {
      close_test_socket(sock);
      if (error_message != nullptr) {
        *error_message = "test client send failed";
      }
      return false;
    }
    if (gap_ms > 0 && index + 1 < payloads.size()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(gap_ms));
    }
  }

  char buffer[64]{};
  const int received = recv(sock, buffer, static_cast<int>(sizeof(buffer)), 0);
  close_test_socket(sock);
  if (received <= 0) {
    if (error_message != nullptr) {
      *error_message = "test client did not receive ack";
    }
    return false;
  }
  if (response != nullptr) {
    response->assign(buffer, buffer + received);
  }
  return true;
}

std::uint32_t result_header_crc(const seat_aoi::ResultSlotHeader* slot) {
  std::array<std::uint8_t, sizeof(seat_aoi::ResultSlotHeader)> bytes{};
  std::memcpy(bytes.data(), slot, bytes.size());
  std::memset(bytes.data(), 0, sizeof(std::uint32_t));
  std::memset(bytes.data() + 20, 0, sizeof(std::uint32_t));
  return seat_aoi::crc32(bytes.data(), bytes.size());
}

std::uint8_t* slot_base(void* data, std::uint32_t slot_index, std::uint32_t slot_size) {
  return static_cast<std::uint8_t*>(data) + sizeof(seat_aoi::ShmHeader) +
         static_cast<std::size_t>(slot_index) * slot_size;
}

seat_aoi::SeatImageBundle make_bundle(std::uint64_t sequence_id) {
  seat_aoi::SeatImageBundle bundle;
  bundle.job_meta.sequence_id = sequence_id;
  bundle.job_meta.trigger_id = 1000 + sequence_id;
  seat_aoi::copy_cstr(bundle.job_meta.seat_id, "SIM");
  seat_aoi::copy_cstr(bundle.job_meta.sku, "seat_a_black_leather");
  seat_aoi::copy_cstr(bundle.job_meta.recipe_id, "seat_a_black_leather_v1");
  bundle.job_meta.view_count = 1;
  bundle.job_meta.frame_count = 1;
  bundle.job_meta.capture_mode = static_cast<std::uint32_t>(seat_aoi::CaptureMode::FixedCamera);
  bundle.job_meta.created_at_us = seat_aoi::now_us();

  seat_aoi::CapturedFrame frame;
  frame.bytes = {80, 81, 82, 83};
  frame.meta.camera_index = 0;
  frame.meta.view_index = 0;
  frame.meta.light_index = 1;
  frame.meta.frame_index = 1;
  frame.meta.light_seq_index = 0;
  frame.meta.width = 2;
  frame.meta.height = 2;
  frame.meta.channels = 1;
  frame.meta.stride_bytes = 2;
  frame.meta.pixel_format = static_cast<std::uint32_t>(seat_aoi::PixelFormat::Mono8);
  frame.meta.bit_depth = 8;
  frame.meta.color_order = static_cast<std::uint32_t>(seat_aoi::ColorOrder::Mono);
  frame.meta.dtype_code = static_cast<std::uint32_t>(seat_aoi::DTypeCode::UInt8);
  frame.meta.timestamp_us = seat_aoi::now_us();
  frame.meta.shot_id = 1000 + sequence_id;
  frame.meta.reserved_u64 = 0;
  frame.meta.exposure_us = 800;
  frame.meta.gain = 1.0F;
  seat_aoi::copy_cstr(frame.meta.camera_id, "TOP_BACK");
  seat_aoi::copy_cstr(frame.meta.view_id, "TOP_BACK");
  seat_aoi::copy_cstr(frame.meta.calibration_id, "calib/simulated_v1");
  bundle.frames.push_back(frame);
  return bundle;
}

bool test_frame_ring_skips_blocked_slot() {
  constexpr const char* kName = "/seat_aoi_test_frame_skip";
  constexpr std::uint32_t kSlotCount = 2;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::FrameRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "frame ring initialize failed\n";
    return false;
  }
  auto* first_slot = reinterpret_cast<seat_aoi::FrameSlotHeader*>(
      slot_base(ring.header(), 0, kSlotSize));
  first_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Reading),
                          std::memory_order_release);

  std::uint64_t sequence_id = 0;
  std::string error;
  const bool ok = ring.publish(make_bundle(7), 50, &sequence_id, &error);
  const auto* second_slot = reinterpret_cast<const seat_aoi::FrameSlotHeader*>(
      slot_base(ring.header(), 1, kSlotSize));
  const bool passed =
      ok && sequence_id == 7 &&
      second_slot->state.load(std::memory_order_acquire) ==
          static_cast<std::uint32_t>(seat_aoi::SlotState::Ready);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "frame ring did not skip blocked slot: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_returns_crc_error_immediately() {
  constexpr const char* kName = "/seat_aoi_test_result_crc";
  constexpr std::uint32_t kSlotCount = 1;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* base = slot_base(ring.header(), 0, kSlotSize);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  slot->sequence_id = 9;
  slot->payload_size = seat_aoi::result_slot_defects_offset();
  slot->payload_crc32 = 1234;
  slot->defect_count = 0;
  slot->reserved = 0;
  slot->result_meta.sequence_id = 9;
  slot->result_meta.trigger_id = 1009;
  seat_aoi::copy_cstr(slot->result_meta.seat_id, "SIM");
  slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK);
  slot->result_meta.defect_count = 0;
  slot->result_meta.quality_pass = 1;
  slot->result_meta.error_code = 0;
  slot->result_meta.elapsed_ms = 1.0F;
  slot->result_meta.reserved = 0;
  slot->header_crc32 = 0;
  slot->header_crc32 = result_header_crc(slot);
  slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                    std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(9, 200, &result, &error_code, &error);
  const bool passed = !ok && error_code == seat_aoi::ErrorCode::CrcMismatch &&
                      error == "result payload CRC mismatch" &&
                      slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring did not return CRC error immediately: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_rejects_payload_size_mismatch() {
  constexpr const char* kName = "/seat_aoi_res_size";
  constexpr std::uint32_t kSlotCount = 1;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* base = slot_base(ring.header(), 0, kSlotSize);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  slot->sequence_id = 19;
  slot->payload_size = seat_aoi::result_slot_defects_offset();
  slot->payload_crc32 = seat_aoi::crc32(base + seat_aoi::result_slot_defects_offset(), 0);
  slot->defect_count = 1;
  slot->reserved = 0;
  slot->result_meta.sequence_id = 19;
  slot->result_meta.trigger_id = 1019;
  seat_aoi::copy_cstr(slot->result_meta.seat_id, "SIM");
  slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::NG);
  slot->result_meta.defect_count = 1;
  slot->result_meta.quality_pass = 1;
  slot->result_meta.error_code = 0;
  slot->result_meta.elapsed_ms = 1.0F;
  slot->result_meta.reserved = 0;
  slot->header_crc32 = 0;
  slot->header_crc32 = result_header_crc(slot);
  slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                    std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(19, 200, &result, &error_code, &error);
  const bool passed = !ok && error_code == seat_aoi::ErrorCode::InvalidPayload &&
                      error == "invalid result payload size or defect count" &&
                      slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring accepted mismatched payload size: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_reclaims_stale_and_bad_slots() {
  constexpr const char* kName = "/seat_aoi_res_reclaim";
  constexpr std::uint32_t kSlotCount = 3;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* stale_base = slot_base(ring.header(), 0, kSlotSize);
  auto* stale_slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(stale_base);
  stale_slot->sequence_id = 3;
  stale_slot->payload_size = seat_aoi::result_slot_defects_offset();
  stale_slot->payload_crc32 = seat_aoi::crc32(stale_base + seat_aoi::result_slot_defects_offset(), 0);
  stale_slot->defect_count = 0;
  stale_slot->result_meta.sequence_id = 3;
  stale_slot->result_meta.trigger_id = 1003;
  seat_aoi::copy_cstr(stale_slot->result_meta.seat_id, "SIM");
  stale_slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK);
  stale_slot->result_meta.quality_pass = 1;
  stale_slot->header_crc32 = 0;
  stale_slot->header_crc32 = result_header_crc(stale_slot);
  stale_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                          std::memory_order_release);

  auto* bad_base = slot_base(ring.header(), 1, kSlotSize);
  auto* bad_slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(bad_base);
  bad_slot->sequence_id = 4;
  bad_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Corrupted),
                        std::memory_order_release);

  auto* current_base = slot_base(ring.header(), 2, kSlotSize);
  auto* current_slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(current_base);
  current_slot->sequence_id = 5;
  current_slot->payload_size = seat_aoi::result_slot_defects_offset();
  current_slot->payload_crc32 = seat_aoi::crc32(current_base + seat_aoi::result_slot_defects_offset(), 0);
  current_slot->defect_count = 0;
  current_slot->result_meta.sequence_id = 5;
  current_slot->result_meta.trigger_id = 1005;
  seat_aoi::copy_cstr(current_slot->result_meta.seat_id, "SIM");
  current_slot->result_meta.decision = static_cast<std::uint32_t>(seat_aoi::InspectionDecision::OK);
  current_slot->result_meta.defect_count = 0;
  current_slot->result_meta.quality_pass = 1;
  current_slot->result_meta.error_code = 0;
  current_slot->result_meta.elapsed_ms = 1.0F;
  current_slot->result_meta.reserved = 0;
  current_slot->header_crc32 = 0;
  current_slot->header_crc32 = result_header_crc(current_slot);
  current_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                            std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(5, 200, &result, &error_code, &error);
  const bool passed = ok && result.meta.sequence_id == 5 &&
                      error_code == seat_aoi::ErrorCode::None &&
                      stale_slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty) &&
                      bad_slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty) &&
                      current_slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring let stale/bad slots block current result: " << error << "\n";
  }
  return passed;
}

bool test_result_ring_returns_current_bad_slot_error() {
  constexpr const char* kName = "/seat_aoi_res_badcur";
  constexpr std::uint32_t kSlotCount = 1;
  constexpr std::uint32_t kSlotSize = 4096;
  seat_aoi::ResultRingBuffer ring;
  if (!ring.initialize(kName, kSlotCount, kSlotSize, true)) {
    std::cerr << "result ring initialize failed\n";
    return false;
  }

  auto* base = slot_base(ring.header(), 0, kSlotSize);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  slot->sequence_id = 5;
  slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Corrupted),
                    std::memory_order_release);

  seat_aoi::InspectionResultPayload result;
  seat_aoi::ErrorCode error_code = seat_aoi::ErrorCode::None;
  std::string error;
  const bool ok = ring.wait_for_result(5, 200, &result, &error_code, &error);
  const bool passed = !ok && error_code == seat_aoi::ErrorCode::CrcMismatch &&
                      error == "result slot marked corrupted" &&
                      slot->state.load(std::memory_order_acquire) ==
                          static_cast<std::uint32_t>(seat_aoi::SlotState::Empty) &&
                      ring.header()->read_index == 1;
  ring.unlink_name();
  ring.close();
  if (!passed) {
    std::cerr << "result ring did not return current bad slot error: " << error << "\n";
  }
  return passed;
}

bool test_ring_layout_mismatch_fails_without_reset() {
  constexpr const char* kName = "/seat_aoi_layout";
  seat_aoi::FrameRingBuffer first;
  if (!first.initialize(kName, 1, 4096, true)) {
    std::cerr << "initial frame ring initialize failed\n";
    return false;
  }

  seat_aoi::FrameRingBuffer second;
  const bool ok = second.initialize(kName, 1, 8192, false);
  second.unlink_name();
  second.close();
  first.unlink_name();
  first.close();
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "frame ring layout mismatch did not fail without reset\n";
  }
  return passed;
}

bool test_signal_trigger_timeout_fails_closed() {
  seat_aoi::SimSignalClient signal;
  seat_aoi::SignalClientConfig config;
  config.simulate_trigger_timeout = true;
  if (!signal.initialize(config)) {
    std::cerr << "signal client initialize failed\n";
    return false;
  }
  seat_aoi::ExternalTrigger trigger;
  std::string error;
  const bool ok = signal.wait_trigger(&trigger, 1, &error);
  const bool passed = !ok && !error.empty();
  if (!passed) {
    std::cerr << "signal trigger timeout did not fail closed: " << error << "\n";
  }
  return passed;
}

bool test_tcp_signal_empty_terminator_combined_start_sn() {
  std::string socket_error;
  const std::uint16_t port = reserve_tcp_test_port(&socket_error);
  if (port == 0) {
    std::cerr << "tcp signal test port reserve failed: " << socket_error << "\n";
    return false;
  }

  seat_aoi::TcpSignalClient signal;
  seat_aoi::SignalClientConfig config;
  config.port = port;
  config.station_id = "LINE1_AOI_TEST";
  config.default_sku = "seat_a_black_leather";
  config.protocol_mode = "start_sn";
  config.delimiter = "|";
  config.terminator = "";
  config.start_command = "start";
  config.sn_ack = "sn_ack";
  if (!signal.initialize(config)) {
    std::cerr << "tcp signal initialize failed: "
              << signal.get_health().message << "\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  std::string wait_error;
  bool accepted = false;
  std::thread waiter([&]() {
    accepted = signal.wait_trigger(&trigger, 2000, &wait_error);
  });

  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  std::string response;
  std::string client_error;
  const bool client_ok = send_tcp_test_payload(port, "start|ABC1234560", &response, &client_error);
  waiter.join();

  const bool passed = client_ok && accepted && response == "sn_ack" &&
                      trigger.trigger_id == 1 &&
                      trigger.seat_id == "LINE1_AOI_TEST_ABC1234560" &&
                      trigger.sku == "seat_a_black_leather";
  if (!passed) {
    std::cerr << "tcp signal empty terminator combined start_sn failed: "
              << "client_ok=" << client_ok
              << " client_error=" << client_error
              << " accepted=" << accepted
              << " wait_error=" << wait_error
              << " response=" << response
              << " seat_id=" << trigger.seat_id << "\n";
  }
  return passed;
}

bool test_tcp_signal_empty_terminator_splits_packed_start_sn() {
  std::string socket_error;
  const std::uint16_t port = reserve_tcp_test_port(&socket_error);
  if (port == 0) {
    std::cerr << "tcp signal packed test port reserve failed: " << socket_error << "\n";
    return false;
  }

  seat_aoi::TcpSignalClient signal;
  seat_aoi::SignalClientConfig config;
  config.port = port;
  config.station_id = "LINE1_AOI_TEST";
  config.default_sku = "seat_a_black_leather";
  config.protocol_mode = "start_sn";
  config.delimiter = "|";
  config.terminator = "";
  config.start_command = "start";
  config.sn_ack = "sn_ack";
  if (!signal.initialize(config)) {
    std::cerr << "tcp signal packed initialize failed: "
              << signal.get_health().message << "\n";
    return false;
  }

  seat_aoi::ExternalTrigger first_trigger;
  std::string first_wait_error;
  bool first_accepted = false;
  std::thread waiter([&]() {
    first_accepted = signal.wait_trigger(&first_trigger, 2000, &first_wait_error);
  });

  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  std::string response;
  std::string client_error;
  const bool client_ok =
      send_tcp_test_payload(port, "start|SN001start|SN002", &response, &client_error);
  waiter.join();

  seat_aoi::ExternalTrigger second_trigger;
  std::string second_wait_error;
  const auto second_started_at = std::chrono::steady_clock::now();
  const bool second_accepted = signal.wait_trigger(&second_trigger, 200, &second_wait_error);
  const auto second_elapsed_ms =
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - second_started_at).count();

  const bool passed = client_ok && first_accepted && second_accepted &&
                      response == "sn_ack" &&
                      first_trigger.trigger_id == 1 &&
                      first_trigger.seat_id == "LINE1_AOI_TEST_SN001" &&
                      second_trigger.trigger_id == 2 &&
                      second_trigger.seat_id == "LINE1_AOI_TEST_SN002" &&
                      second_elapsed_ms < 50;
  if (!passed) {
    std::cerr << "tcp signal packed start_sn split failed: "
              << "client_ok=" << client_ok
              << " client_error=" << client_error
              << " first_accepted=" << first_accepted
              << " first_wait_error=" << first_wait_error
              << " second_accepted=" << second_accepted
              << " second_wait_error=" << second_wait_error
              << " response=" << response
              << " first_seat_id=" << first_trigger.seat_id
              << " second_seat_id=" << second_trigger.seat_id
              << " second_elapsed_ms=" << second_elapsed_ms << "\n";
  }
  return passed;
}

bool test_tcp_signal_empty_terminator_splits_quick_separate_start_sn() {
  std::string socket_error;
  const std::uint16_t port = reserve_tcp_test_port(&socket_error);
  if (port == 0) {
    std::cerr << "tcp signal quick separate test port reserve failed: "
              << socket_error << "\n";
    return false;
  }

  seat_aoi::TcpSignalClient signal;
  seat_aoi::SignalClientConfig config;
  config.port = port;
  config.station_id = "LINE1_AOI_TEST";
  config.default_sku = "seat_a_black_leather";
  config.protocol_mode = "start_sn";
  config.delimiter = "|";
  config.terminator = "";
  config.start_command = "start";
  config.sn_ack = "sn_ack";
  if (!signal.initialize(config)) {
    std::cerr << "tcp signal quick separate initialize failed: "
              << signal.get_health().message << "\n";
    return false;
  }

  seat_aoi::ExternalTrigger first_trigger;
  std::string first_wait_error;
  bool first_accepted = false;
  std::thread waiter([&]() {
    first_accepted = signal.wait_trigger(&first_trigger, 2000, &first_wait_error);
  });

  std::this_thread::sleep_for(std::chrono::milliseconds(50));
  std::string response;
  std::string client_error;
  const bool client_ok =
      send_tcp_test_payload_chunks(port,
                                   {"start|SN101", "start|SN102"},
                                   20,
                                   &response,
                                   &client_error);
  waiter.join();

  seat_aoi::ExternalTrigger second_trigger;
  std::string second_wait_error;
  const bool second_accepted = signal.wait_trigger(&second_trigger, 200, &second_wait_error);

  const bool passed = client_ok && first_accepted && second_accepted &&
                      response == "sn_ack" &&
                      first_trigger.trigger_id == 1 &&
                      first_trigger.seat_id == "LINE1_AOI_TEST_SN101" &&
                      second_trigger.trigger_id == 2 &&
                      second_trigger.seat_id == "LINE1_AOI_TEST_SN102";
  if (!passed) {
    std::cerr << "tcp signal quick separate start_sn split failed: "
              << "client_ok=" << client_ok
              << " client_error=" << client_error
              << " first_accepted=" << first_accepted
              << " first_wait_error=" << first_wait_error
              << " second_accepted=" << second_accepted
              << " second_wait_error=" << second_wait_error
              << " response=" << response
              << " first_seat_id=" << first_trigger.seat_id
              << " second_seat_id=" << second_trigger.seat_id << "\n";
  }
  return passed;
}

bool test_station_fault_returns_recheck(const std::string& name,
                                        seat_aoi::StationConfig config,
                                        seat_aoi::ErrorCode expected_error) {
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
  };
  const auto storage_root =
      std::filesystem::temp_directory_path() /
      ("seat_aoi_fault_check_" + std::to_string(seat_aoi::now_us()));
  config.trace_root = (storage_root / "trace").string();
  config.image_save.enabled = false;
  config.image_save.root_dir = (storage_root / "images").string();
  config.image_save.cleanup_enabled = false;
  config.image_save.cleanup_min_free_ratio = 0.0F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << name << " station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7001;
  trigger.seat_id = "SIM_FAULT";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();

  const auto decision = static_cast<seat_aoi::InspectionDecision>(result.meta.decision);
  const auto error_code = static_cast<seat_aoi::ErrorCode>(result.meta.error_code);
  const bool passed = decision == seat_aoi::InspectionDecision::Recheck &&
                      error_code == expected_error &&
                      result.meta.quality_pass == 0 &&
                      result.meta.defect_count == 0;
  if (!passed) {
    std::cerr << name << " did not return RECHECK expected_error="
              << static_cast<std::uint32_t>(expected_error)
              << " actual_decision=" << result.meta.decision
              << " actual_error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_light_fault_returns_recheck() {
  seat_aoi::StationConfig config;
  config.simulate_light_fault = true;
  return test_station_fault_returns_recheck("light fault",
                                            config,
                                            seat_aoi::ErrorCode::LightFault);
}

bool test_missing_frame_returns_recheck() {
  seat_aoi::StationConfig config;
  config.simulate_missing_frame = true;
  return test_station_fault_returns_recheck("missing frame",
                                            config,
                                            seat_aoi::ErrorCode::MissingFrame);
}

bool test_frame_slot_unavailable_returns_recheck() {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 65536;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 1;
  config.detector_timeout_ms = 1;
  config.trigger_timeout_ms = 1;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.light_order = {1, 2, 3};
  const auto storage_root =
      std::filesystem::temp_directory_path() /
      ("seat_aoi_slot_check_" + std::to_string(seat_aoi::now_us()));
  config.trace_root = (storage_root / "trace").string();
  config.image_save.enabled = false;
  config.image_save.root_dir = (storage_root / "images").string();
  config.image_save.cleanup_enabled = false;
  config.image_save.cleanup_min_free_ratio = 0.0F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    const auto health = station.health_snapshot();
    std::cerr << "slot unavailable station initialize failed state="
              << seat_aoi::station_state_name(health.state)
              << " alarm=" << health.alarm_message << "\n";
    return false;
  }

  seat_aoi::SharedMemory frame_shm;
  if (!frame_shm.open_existing(
          seat_aoi::kFrameShmName,
          seat_aoi::shared_memory_total_size(config.slot_count, config.frame_slot_size))) {
    station.cleanup_shared_memory();
    std::cerr << "slot unavailable frame shm open failed\n";
    return false;
  }
  auto* blocked_slot = reinterpret_cast<seat_aoi::FrameSlotHeader*>(
      slot_base(frame_shm.data(), 0, config.frame_slot_size));
  blocked_slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Reading),
                            std::memory_order_release);

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7002;
  trigger.seat_id = "SIM_SLOT_FULL";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  frame_shm.close();
  station.cleanup_shared_memory();

  const auto decision = static_cast<seat_aoi::InspectionDecision>(result.meta.decision);
  const auto error_code = static_cast<seat_aoi::ErrorCode>(result.meta.error_code);
  const bool passed = decision == seat_aoi::InspectionDecision::Recheck &&
                      error_code == seat_aoi::ErrorCode::SlotUnavailable &&
                      result.meta.quality_pass == 0 &&
                      result.meta.defect_count == 0;
  if (!passed) {
    std::cerr << "slot unavailable did not return RECHECK actual_decision="
              << result.meta.decision << " actual_error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_runtime_light_channel_config_parses() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  if (!seat_aoi::load_station_runtime_config(
          "cpp_controller/config/station_runtime.test.conf", &config, &error)) {
    error.clear();
    if (!seat_aoi::load_station_runtime_config(
            "config/station_runtime.test.conf", &config, &error)) {
      std::cerr << "runtime config parse failed: " << error << "\n";
      return false;
    }
  }
  const bool passed = config.hardware_mode == seat_aoi::HardwareMode::Lab &&
                      config.capture_mode == seat_aoi::CaptureMode::FixedCamera &&
                      config.capture_schedule == seat_aoi::CaptureSchedule::SharedLightParallel &&
                      config.camera_backend == seat_aoi::HardwareBackend::HikrobotMvs &&
                      config.lights[0].backend == seat_aoi::HardwareBackend::SerialAscii &&
                      config.light_order.size() == 3 &&
                      config.light_order[0] == 1 &&
                      config.light_order[1] == 2 &&
                      config.light_order[2] == 3 &&
                      config.light_channels.size() >= 3 &&
                      config.light_channels[0].light_index == 1 &&
                      config.light_channels[0].physical_channel == 1 &&
                      config.light_channels[0].exposure_us == 50000 &&
                      config.light_channels[0].strobe_width_us == 900 &&
                      config.light_channels[0].trigger_delay_us == 99 &&
                      config.light_channels[0].gain == 1.0F &&
                      config.light_channels[0].current_percent == 100.0F &&
                      config.light_channels[1].light_index == 2 &&
                      config.light_channels[1].physical_channel == 2 &&
                      config.light_channels[1].strobe_width_us == 950 &&
                      config.light_channels[2].light_index == 3 &&
                      config.light_channels[2].physical_channel == 3 &&
                      config.light_channels[2].strobe_width_us == 999 &&
                      config.light_channels[2].current_percent == 100.0F &&
                      config.lights[0].serial_port == "COM1" &&
                      config.lights[0].baud_rate == 9600 &&
                      config.lights[0].trigger_input_line == "F1" &&
                      config.max_camera_failures_before_reset == 2 &&
                      config.lights[0].response_mode ==
                          seat_aoi::LightSerialResponseMode::Ack &&
                      config.signal.terminator == "\n" &&
                      config.signal.ok_response == "ok\n" &&
                      config.signal.start_ack == "start_ack\n" &&
                      config.signal.sn_ack == "sn_ack\n" &&
                      config.image_save.enabled &&
                      config.image_save.cleanup_enabled &&
                      config.image_save.cleanup_min_free_ratio == 0.20F;
  if (!passed) {
    std::cerr << "runtime light channel config did not parse expected values\n";
  }
  return passed;
}

bool test_runtime_multi_light_controller_config_rejected() {
  const std::string path =
      (std::filesystem::temp_directory_path() /
       ("seat_aoi_multi_light_config_" + std::to_string(seat_aoi::now_us()) + ".conf"))
          .string();
  {
    std::ofstream out(path);
    out << "hardware_mode=simulated\n"
        << "signal.backend=simulated\n"
        << "camera.backend=simulated\n"
        << "light.backend=simulated\n"
        << "light.response_mode=none\n"
        << "light.1.backend=simulated\n"
        << "light.1.response_mode=ack\n"
        << "slot_count=4\n"
        << "frame_slot_size=16777216\n"
        << "result_slot_size=65536\n"
        << "publish_timeout_ms=1000\n"
        << "detector_timeout_ms=5000\n"
        << "trigger_timeout_ms=1000\n"
        << "camera_timeout_ms=200\n"
        << "light_timeout_ms=200\n"
        << "warning_recheck_threshold=3\n"
        << "critical_recheck_threshold=5\n"
        << "light_order=1,2\n"
        << "light.1.physical_channel=1\n"
        << "light.1.exposure_us=50000\n"
        << "light.1.strobe_width_us=900\n"
        << "light.1.trigger_delay_us=99\n"
        << "light.1.gain=1.0\n"
        << "light.1.current_percent=100\n"
        << "light.1.2.physical_channel=1\n"
        << "light.1.2.exposure_us=900\n"
        << "light.1.2.strobe_width_us=750\n"
        << "light.1.2.trigger_delay_us=20\n"
        << "light.1.2.gain=1.1\n"
        << "light.1.2.current_percent=55\n";
  }
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  const bool ok = seat_aoi::load_station_runtime_config(path, &config, &error);
  std::remove(path.c_str());
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "multi light controller config was not rejected\n";
  }
  return passed;
}

void write_replay_png_group(const std::filesystem::path& root,
                            const std::string& camera_id,
                            int sample_index,
                            std::uint32_t width,
                            std::uint32_t height) {
  std::filesystem::create_directories(root);
  for (std::uint32_t light = 1; light <= 3; ++light) {
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(width) * height);
    for (std::uint32_t y = 0; y < height; ++y) {
      for (std::uint32_t x = 0; x < width; ++x) {
        bytes[static_cast<std::size_t>(y) * width + x] =
            static_cast<std::uint8_t>(40 + sample_index * 10 + light * 20 + x + y);
      }
    }
    const auto path = root / (camera_id + "_" +
                              std::to_string(1000000 + sample_index * 100 + light) +
                              "_L" + std::to_string(light) + "_original.png");
    std::string error;
    if (!seat_aoi::write_png(path.string(), bytes, width, height, &error)) {
      std::cerr << "failed to write replay test png: " << error << "\n";
    }
  }
}

bool test_replay_capture_config_parses() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  bool ok = seat_aoi::load_station_runtime_config(
      "cpp_controller/config/station_runtime.replay_capture.conf", &config, &error);
  if (!ok) {
    error.clear();
    ok = seat_aoi::load_station_runtime_config(
        "config/station_runtime.replay_capture.conf", &config, &error);
  }
  const bool passed = ok &&
                      config.hardware_mode == seat_aoi::HardwareMode::Simulated &&
                      config.camera_backend == seat_aoi::HardwareBackend::Simulated &&
                      config.cameras.size() == 2 &&
                      config.cameras[0].replay_random &&
                      config.cameras[1].replay_random &&
                      !config.cameras[0].replay_root.empty() &&
                      config.frame_slot_size == 134217728U &&
                      config.recipe_id == "seat_a_black_leather_production_v1";
  if (!passed) {
    std::cerr << "replay capture config did not parse: " << error << "\n";
  }
  return passed;
}

bool test_replay_config_rejected_outside_simulated_camera_backend() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_replay_config_reject_" + std::to_string(seat_aoi::now_us()));
  std::filesystem::create_directories(root);
  const auto path = root / "station_runtime.invalid_replay.conf";
  std::ofstream config(path);
  config
      << "hardware_mode=lab\n"
      << "signal.backend=manual_trigger\n"
      << "camera.backend=hikrobot_mvs\n"
      << "light.backend=simulated\n"
      << "camera.0.camera_id=TOP_BACK\n"
      << "camera.0.serial_number=REPLAY_TOP_BACK\n"
      << "camera.0.calibration_id=calib/top_back_production_v1\n"
      << "camera.0.width=4\n"
      << "camera.0.height=3\n"
      << "camera.0.channels=1\n"
      << "camera.0.pixel_format=Mono8\n"
      << "camera.0.replay_root=" << root.string() << "\n"
      << "light.1.physical_channel=1\n";
  config.close();

  seat_aoi::StationRuntimeConfig runtime_config;
  std::string error;
  const bool loaded = seat_aoi::load_station_runtime_config(
      path.string(), &runtime_config, &error);
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  const bool passed = !loaded && error.find("replay_*") != std::string::npos;
  if (!passed) {
    std::cerr << "replay config outside simulated backend was not rejected: "
              << error << "\n";
  }
  return passed;
}

bool test_replay_camera_reads_selected_png_group() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_replay_ok_" + std::to_string(seat_aoi::now_us()));
  write_replay_png_group(root, "TOP_BACK", 1, 4, 3);
  write_replay_png_group(root, "TOP_BACK", 2, 4, 3);

  seat_aoi::CameraConfig config;
  config.camera_index = 0;
  config.camera_id = "TOP_BACK";
  config.width = 4;
  config.height = 3;
  config.channels = 1;
  config.pixel_format = "Mono8";
  config.replay_root = root.string();
  config.replay_sample_index = 2;
  config.replay_required_lights = {1, 2, 3};

  seat_aoi::CameraDevice camera;
  const bool initialized = camera.initialize(config);
  seat_aoi::LightChannelParam light;
  light.light_index = 2;
  light.exposure_us = 800;
  light.gain = 1.0F;
  seat_aoi::CapturedFrame frame;
  const bool captured = initialized && camera.arm(1001, light, 1, 10) &&
                        camera.capture(1001, light, 1, &frame, 10);
  const bool passed = captured &&
                      frame.bytes.size() == 12 &&
                      frame.bytes[0] == static_cast<std::uint8_t>(40 + 2 * 10 + 2 * 20) &&
                      frame.meta.width == 4 &&
                      frame.meta.height == 3 &&
                      frame.meta.light_index == 2 &&
                      frame.meta.frame_index == 100101;
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  if (!passed) {
    std::cerr << "replay camera did not read selected png group health="
              << camera.get_health().message << "\n";
  }
  return passed;
}

bool test_replay_camera_fails_when_light_group_missing() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_replay_missing_" + std::to_string(seat_aoi::now_us()));
  write_replay_png_group(root, "TOP_BACK", 1, 4, 3);
  std::filesystem::remove(root / "TOP_BACK_1000103_L3_original.png");

  seat_aoi::CameraConfig config;
  config.camera_index = 0;
  config.camera_id = "TOP_BACK";
  config.width = 4;
  config.height = 3;
  config.channels = 1;
  config.pixel_format = "Mono8";
  config.replay_root = root.string();
  config.replay_sample_index = 1;
  config.replay_required_lights = {1, 2, 3};

  seat_aoi::CameraDevice camera;
  const bool initialized = camera.initialize(config);
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  const bool passed = !initialized &&
                      camera.get_health().message.find("missing required light") != std::string::npos;
  if (!passed) {
    std::cerr << "missing replay light group did not fail: "
              << camera.get_health().message << "\n";
  }
  return passed;
}

bool test_replay_camera_fails_when_selected_sample_is_incomplete() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_replay_incomplete_" + std::to_string(seat_aoi::now_us()));
  write_replay_png_group(root, "TOP_BACK", 1, 4, 3);
  write_replay_png_group(root, "TOP_BACK", 2, 4, 3);
  std::filesystem::remove(root / "TOP_BACK_1000203_L3_original.png");

  seat_aoi::CameraConfig config;
  config.camera_index = 0;
  config.camera_id = "TOP_BACK";
  config.width = 4;
  config.height = 3;
  config.channels = 1;
  config.pixel_format = "Mono8";
  config.replay_root = root.string();
  config.replay_sample_index = 2;
  config.replay_required_lights = {1, 2, 3};

  seat_aoi::CameraDevice camera;
  const bool initialized = camera.initialize(config);
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  const bool passed = !initialized &&
                      camera.get_health().message.find("incomplete") != std::string::npos;
  if (!passed) {
    std::cerr << "selected incomplete replay sample did not fail: "
              << camera.get_health().message << "\n";
  }
  return passed;
}

bool test_replay_camera_fails_on_shape_mismatch() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_replay_shape_" + std::to_string(seat_aoi::now_us()));
  write_replay_png_group(root, "TOP_BACK", 1, 4, 3);

  seat_aoi::CameraConfig config;
  config.camera_index = 0;
  config.camera_id = "TOP_BACK";
  config.width = 5;
  config.height = 3;
  config.channels = 1;
  config.pixel_format = "Mono8";
  config.replay_root = root.string();
  config.replay_sample_index = 1;
  config.replay_required_lights = {1, 2, 3};

  seat_aoi::CameraDevice camera;
  const bool initialized = camera.initialize(config);
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  const bool passed = !initialized &&
                      camera.get_health().message.find("shape mismatch") != std::string::npos;
  if (!passed) {
    std::cerr << "replay shape mismatch did not fail: "
              << camera.get_health().message << "\n";
  }
  return passed;
}

bool test_replay_station_random_selects_complete_sample_pool() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_replay_complete_" + std::to_string(seat_aoi::now_us()));
  write_replay_png_group(root, "TOP_BACK", 1, 4, 3);
  write_replay_png_group(root, "TOP_BACK", 2, 4, 3);
  write_replay_png_group(root, "TOP_CUSHION", 1, 4, 3);
  write_replay_png_group(root, "TOP_CUSHION", 2, 4, 3);
  std::filesystem::remove(root / "TOP_CUSHION_1000203_L3_original.png");

  seat_aoi::StationConfig config;
  config.controller_mode = seat_aoi::ControllerMode::CaptureOnly;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 20;
  config.light_timeout_ms = 20;
  config.arm_settle_ms = 0;
  config.trace_root = (root / "trace").string();
  config.light_order = {1, 2, 3};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
      seat_aoi::RuntimeLightChannelConfig{0, 2, 2, 800, 800, 10, 1.0F, 60.0F},
      seat_aoi::RuntimeLightChannelConfig{0, 3, 3, 800, 800, 10, 1.0F, 55.0F},
  };
  config.image_save.enabled = false;
  config.image_save.cleanup_enabled = false;
  config.cameras = {
      seat_aoi::RuntimeCameraConfig{0, "TOP_BACK", "", "calib/simulated_v1", 4, 3, 1, "Mono8", "", "", 8, false},
      seat_aoi::RuntimeCameraConfig{1, "TOP_CUSHION", "", "calib/simulated_v1", 4, 3, 1, "Mono8", "", "", 8, false},
  };
  for (auto& camera : config.cameras) {
    camera.replay_root = root.string();
    camera.replay_random = true;
  }

  seat_aoi::StationController station;
  const bool initialized = station.initialize(config);
  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7040;
  trigger.seat_id = "SIM_REPLAY_COMPLETE";
  trigger.sku = "seat_a_black_leather";
  const auto result = initialized ? station.inspect_one_seat(trigger) : seat_aoi::InspectionResultPayload{};
  station.cleanup_shared_memory();
  std::error_code ec;
  std::filesystem::remove_all(root, ec);

  const bool passed = initialized &&
                      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
                          seat_aoi::InspectionDecision::Recheck &&
                      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
                          seat_aoi::ErrorCode::None;
  if (!passed) {
    std::cerr << "replay station did not select complete shared sample pool: decision="
              << result.meta.decision << " error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_station_storage_failure_returns_recheck_before_capture() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_storage_fail_" + std::to_string(seat_aoi::now_us()));
  const auto trace_root = root / "trace";
  const auto image_root = root / "images_as_file";
  std::filesystem::create_directories(trace_root);
  {
    std::ofstream(image_root) << "not a directory";
  }
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
  };
  config.trace_root = trace_root.string();
  config.image_save.enabled = true;
  config.image_save.root_dir = image_root.string();
  config.image_save.cleanup_enabled = true;
  config.image_save.cleanup_trace_root = true;
  config.image_save.cleanup_min_free_ratio = 0.20F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::error_code ec;
    std::filesystem::remove_all(root, ec);
    std::cerr << "storage failure station initialize failed\n";
    return false;
  }
  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7021;
  trigger.seat_id = "SIM_STORAGE_FAIL";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();
  std::error_code ec;
  std::filesystem::remove_all(root, ec);

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::DeviceFault;
  if (!passed) {
    std::cerr << "storage failure did not return device RECHECK: decision="
              << result.meta.decision << " error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_capture_only_bypasses_shared_memory_and_saves_images() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_capture_only_" + std::to_string(seat_aoi::now_us()));
  seat_aoi::StationConfig config;
  config.controller_mode = seat_aoi::ControllerMode::CaptureOnly;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 65536;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.trace_root = (root / "trace").string();
  config.image_save.enabled = true;
  config.image_save.root_dir = (root / "images").string();
  config.image_save.save_original = true;
  config.image_save.cleanup_enabled = true;
  config.image_save.cleanup_trace_root = true;
  config.image_save.cleanup_min_free_ratio = 0.0F;
  config.light_order = {1, 2, 3};

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::error_code ec;
    std::filesystem::remove_all(root, ec);
    std::cerr << "capture_only station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7030;
  trigger.seat_id = "SIM_CAPTURE_ONLY";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();

  std::size_t image_count = 0;
  std::error_code walk_ec;
  if (std::filesystem::exists(root / "images")) {
    for (const auto& entry :
         std::filesystem::recursive_directory_iterator(root / "images", walk_ec)) {
      if (!walk_ec && entry.is_regular_file() && entry.path().extension() == ".png") {
        ++image_count;
      }
    }
  }

  seat_aoi::SharedMemory frame_shm;
  const bool frame_shm_opened =
      frame_shm.open_existing(seat_aoi::kFrameShmName,
                              seat_aoi::shared_memory_total_size(config.slot_count,
                                                                 config.frame_slot_size));
  frame_shm.close();
  seat_aoi::SharedMemory result_shm;
  const bool result_shm_opened =
      result_shm.open_existing(seat_aoi::kResultShmName,
                               seat_aoi::shared_memory_total_size(config.slot_count,
                                                                  config.result_slot_size));
  result_shm.close();

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::None &&
      image_count == 6 &&
      !frame_shm_opened &&
      !result_shm_opened;

  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  if (!passed) {
    std::cerr << "capture_only did not bypass shm or save expected images: images="
              << image_count << " frame_shm_opened=" << frame_shm_opened
              << " result_shm_opened=" << result_shm_opened
              << " decision=" << result.meta.decision
              << " error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_invalid_light_controller_index_rejected() {
  seat_aoi::StationRuntimeConfig config;
  config.light_order = {1, 2, 3};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{1, 1, 1, 800, 800, 10, 1.0F, 60.0F},
      seat_aoi::RuntimeLightChannelConfig{0, 2, 2, 800, 800, 10, 1.0F, 60.0F},
      seat_aoi::RuntimeLightChannelConfig{0, 3, 3, 800, 800, 10, 1.0F, 55.0F},
  };
  config.lights = {seat_aoi::RuntimeLightConfig{}};
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "invalid light controller index was not rejected\n";
  }
  return passed;
}

bool test_non_strobe_light_order_rejected() {
  auto config = make_filled_production_runtime_config();
  config.light_order = {12, 1, 2, 3};
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "non 1,2,3 light order was not rejected\n";
  }
  return passed;
}
bool test_image_save_path_uses_date_directory() {
  seat_aoi::ImageSaveConfig config;
  config.enabled = true;
  config.root_dir = "images";
  seat_aoi::CapturedFrame frame;
  frame.meta.timestamp_us = 1234567;
  frame.meta.light_index = 2;
  seat_aoi::copy_cstr(frame.meta.camera_id, "TOP/BACK");

  const std::string path =
      seat_aoi::build_original_image_path(config, "20260619", "SEAT:001", frame);

  const bool passed =
      path.find("images") != std::string::npos &&
      path.find("20260619") != std::string::npos &&
      path.find("SEAT_001") != std::string::npos &&
      path.find("TOP_BACK_1234567_L2_original.png") != std::string::npos;
  if (!passed) {
    std::cerr << "image save path did not include sanitized date/seat/camera: " << path
              << "\n";
  }
  return passed;
}

bool test_image_save_cleanup_removes_files_without_deleting_date_dirs() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_image_cleanup_" + std::to_string(seat_aoi::now_us()));
  constexpr std::size_t kOldestFileBytes = 16U * 1024U * 1024U;
  std::filesystem::create_directories(root / "20250101" / "OLD_SEAT");
  std::filesystem::create_directories(root / "20260619" / "CURRENT_SEAT");
  std::filesystem::create_directories(root / "misc");
  {
    std::ofstream oldest(root / "20250101" / "OLD_SEAT" / "oldest.png", std::ios::binary);
    const std::string chunk(1024U * 1024U, '\0');
    for (std::size_t written = 0; written < kOldestFileBytes; written += chunk.size()) {
      oldest.write(chunk.data(), static_cast<std::streamsize>(chunk.size()));
    }
    std::ofstream(root / "20250101" / "OLD_SEAT" / "newer.png") << "newer";
    std::ofstream(root / "20260619" / "CURRENT_SEAT" / "current.png") << "current";
    std::ofstream(root / "misc" / "keep.txt") << "keep";
  }
  const auto base_time = std::filesystem::file_time_type::clock::now();
  std::filesystem::last_write_time(root / "20250101" / "OLD_SEAT" / "oldest.png",
                                   base_time - std::chrono::hours(4));
  std::filesystem::last_write_time(root / "20250101" / "OLD_SEAT" / "newer.png",
                                   base_time - std::chrono::hours(3));
  std::filesystem::last_write_time(root / "20260619" / "CURRENT_SEAT" / "current.png",
                                   base_time - std::chrono::hours(1));

  seat_aoi::ImageSaveConfig config;
  config.enabled = true;
  config.save_original = true;
  config.cleanup_enabled = true;
  config.root_dir = root.string();
  config.cleanup_min_free_ratio = 1.0F;
  std::string message;
  const bool ok = seat_aoi::cleanup_old_image_data_if_needed(config, &message);

  const bool passed = ok &&
                      !std::filesystem::exists(root / "20250101" / "OLD_SEAT" / "oldest.png") &&
                      !std::filesystem::exists(root / "20250101" / "OLD_SEAT" / "newer.png") &&
                      !std::filesystem::exists(root / "20260619" / "CURRENT_SEAT" / "current.png") &&
                      std::filesystem::exists(root / "20250101") &&
                      std::filesystem::exists(root / "20260619") &&
                      std::filesystem::exists(root / "misc" / "keep.txt");
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  if (!passed) {
    std::cerr << "image cleanup did not remove files while keeping date roots: " << message << "\n";
  }
  return passed;
}

bool test_runtime_storage_cleanup_removes_trace_date_files() {
  const auto root = std::filesystem::temp_directory_path() /
                    ("seat_aoi_trace_cleanup_" + std::to_string(seat_aoi::now_us()));
  const auto image_root = root / "unused_images";
  const auto trace_root = root / "trace";
  std::filesystem::create_directories(trace_root / "20250101" / "OLD_SEAT_1");
  std::filesystem::create_directories(trace_root / "misc");
  {
    std::ofstream(trace_root / "20250101" / "OLD_SEAT_1" / "raw.png") << "raw";
    std::ofstream(trace_root / "display_latest.json") << "{}";
    std::ofstream(trace_root / "misc" / "keep.txt") << "keep";
  }
  seat_aoi::ImageSaveConfig config;
  config.enabled = false;
  config.root_dir = image_root.string();
  config.cleanup_enabled = true;
  config.cleanup_trace_root = true;
  config.cleanup_min_free_ratio = 1.0F;
  std::string message;
  const bool ok = seat_aoi::cleanup_runtime_storage_if_needed(
      config, trace_root.string(), &message);
  const bool passed = ok &&
                      !std::filesystem::exists(trace_root / "20250101" / "OLD_SEAT_1" / "raw.png") &&
                      std::filesystem::exists(trace_root / "20250101") &&
                      std::filesystem::exists(trace_root / "display_latest.json") &&
                      std::filesystem::exists(trace_root / "misc" / "keep.txt") &&
                      !std::filesystem::exists(image_root);
  std::error_code ec;
  std::filesystem::remove_all(root, ec);
  if (!passed) {
    std::cerr << "runtime storage cleanup did not clean trace date files safely: "
              << message << "\n";
  }
  return passed;
}

bool test_single_camera_config_validates() {
  const std::string path =
      (std::filesystem::temp_directory_path() /
       ("seat_aoi_single_camera_config_" + std::to_string(seat_aoi::now_us()) + ".conf"))
          .string();
  {
    std::ofstream out(path);
    out << "hardware_mode=lab\n"
        << "signal.backend=manual_trigger\n"
        << "camera.backend=simulated\n"
        << "light.backend=simulated\n"
        << "slot_count=4\n"
        << "frame_slot_size=67108864\n"
        << "result_slot_size=65536\n"
        << "publish_timeout_ms=1000\n"
        << "detector_timeout_ms=5000\n"
        << "trigger_timeout_ms=1000\n"
        << "camera_timeout_ms=300\n"
        << "light_timeout_ms=300\n"
        << "warning_recheck_threshold=3\n"
        << "critical_recheck_threshold=5\n"
        << "max_jobs=1\n"
        << "recipe_id=seat_a_black_leather_production_v1\n"
        << "controller_mode=online\n"
        << "capture_mode=fixed_camera\n"
        << "capture_schedule=shared_light_parallel\n"
        << "light_order=1,2,3\n"
        << "trace_root=trace\n"
        << "signal.station_id=LAB_AOI_01\n"
        << "signal.default_sku=seat_a_black_leather\n"
        << "camera.0.camera_id=TOP_BACK\n"
        << "camera.0.serial_number=MVCH120_TEST_SN\n"
        << "camera.0.calibration_id=calib/top_back_production_v1\n"
        << "camera.0.width=4096\n"
        << "camera.0.height=3072\n"
        << "camera.0.channels=1\n"
        << "camera.0.pixel_format=Mono8\n"
        << "camera.0.trigger_line=Line0\n"
        << "camera.0.exposure_output_line=Line1\n"
        << "camera.0.buffer_count=8\n"
        << "light.device_id=FL-ACDH-20048-4\n"
        << "light.baud_rate=9600\n"
        << "light.trigger_input_line=F1\n"
        << "light.1.physical_channel=1\n"
        << "light.1.exposure_us=50000\n"
        << "light.1.strobe_width_us=900\n"
        << "light.1.trigger_delay_us=99\n"
        << "light.1.gain=1.0\n"
        << "light.1.current_percent=100\n"
        << "light.2.physical_channel=2\n"
        << "light.2.exposure_us=50000\n"
        << "light.2.strobe_width_us=950\n"
        << "light.2.trigger_delay_us=99\n"
        << "light.2.gain=1.0\n"
        << "light.2.current_percent=100\n"
        << "light.3.physical_channel=3\n"
        << "light.3.exposure_us=50000\n"
        << "light.3.strobe_width_us=999\n"
        << "light.3.trigger_delay_us=99\n"
        << "light.3.gain=1.0\n"
        << "light.3.current_percent=100\n"
        << "light.response_mode=ack\n";
  }
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  const bool ok = seat_aoi::load_station_runtime_config(path, &config, &error);
  std::remove(path.c_str());
  const bool passed = ok && config.cameras.size() == 1 &&
                      config.cameras[0].camera_index == 0 &&
                      config.light_order.size() == 3;
  if (!passed) {
    std::cerr << "single camera config did not validate expected fixed-camera setup: "
              << error << "\n";
  }
  return passed;
}
bool test_production_config_file_validates() {
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  bool ok = seat_aoi::load_station_runtime_config(
      "cpp_controller/config/station_runtime.production.conf", &config, &error);
  if (!ok) {
    error.clear();
    ok = seat_aoi::load_station_runtime_config(
        "config/station_runtime.production.conf", &config, &error);
  }
  const bool passed = ok &&
                      config.hardware_mode == seat_aoi::HardwareMode::Production &&
                      config.signal.backend == seat_aoi::HardwareBackend::TcpSignal &&
                      config.camera_backend == seat_aoi::HardwareBackend::HikrobotMvs &&
                      config.lights[0].backend == seat_aoi::HardwareBackend::SerialAscii &&
                      config.capture_mode == seat_aoi::CaptureMode::FixedCamera &&
                      config.capture_schedule == seat_aoi::CaptureSchedule::SharedLightParallel &&
                      config.light_order.size() == 3 &&
                      config.light_order[0] == 1 &&
                      config.light_order[1] == 2 &&
                      config.light_order[2] == 3 &&
                      config.cameras.size() == 2 &&
                      config.light_channels.size() >= 3 &&
                      config.lights[0].serial_port == "COM1" &&
                      config.lights[0].baud_rate == 9600 &&
                      config.lights[0].trigger_input_line == "F1" &&
                      config.max_camera_failures_before_reset == 2 &&
                      config.signal.protocol_mode == "start_sn" &&
                      config.signal.delimiter == "|" &&
                      config.signal.terminator.empty() &&
                      config.signal.start_ack == "start_ack" &&
                      config.signal.sn_ack == "sn_ack" &&
                      config.lights[0].response_mode ==
                          seat_aoi::LightSerialResponseMode::Ack;
  if (!passed) {
    std::cerr << "production config did not validate expected fixed-camera strobe setup: "
              << error << "\n";
  }
  return passed;
}

seat_aoi::StationRuntimeConfig make_filled_production_runtime_config() {
  seat_aoi::StationRuntimeConfig config;
  config.hardware_mode = seat_aoi::HardwareMode::Production;
  config.signal.backend = seat_aoi::HardwareBackend::ExternalSignal;
  config.camera_backend = seat_aoi::HardwareBackend::HikrobotMvs;
  config.capture_schedule = seat_aoi::CaptureSchedule::SharedLightParallel;
  config.lights[0].backend = seat_aoi::HardwareBackend::SerialAscii;
  config.frame_slot_size = 64 * 1024 * 1024;
  config.signal.station_id = "LINE1_AOI_01";
  config.signal.default_seat_id = "EXTERNAL_SEAT";
  config.signal.default_sku = "seat_a_black_leather";
  config.lights[0].device_id = "STROBE_01";
  config.lights[0].serial_port = "COM1";
  config.lights[0].baud_rate = 9600;
  config.lights[0].trigger_input_line = "F1";
  config.light_channels[0].exposure_us = 50000;
  config.light_channels[0].strobe_width_us = 900;
  config.light_channels[0].trigger_delay_us = 99;
  config.light_channels[1].exposure_us = 50000;
  config.light_channels[1].strobe_width_us = 950;
  config.light_channels[1].trigger_delay_us = 99;
  config.light_channels[2].exposure_us = 50000;
  config.light_channels[2].strobe_width_us = 999;
  config.light_channels[2].trigger_delay_us = 99;
  for (auto& camera : config.cameras) {
    camera.serial_number = "CAM_SN_" + std::to_string(camera.camera_index);
    camera.trigger_line = "Line0";
    camera.exposure_output_line = "Line1";
  }
  return config;
}

bool test_filled_production_config_validates() {
  auto config = make_filled_production_runtime_config();
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  if (!ok) {
    std::cerr << "filled production config did not validate: " << error << "\n";
  }
  return ok;
}

bool test_camera_buffer_count_must_cover_light_order() {
  auto config = make_filled_production_runtime_config();
  config.cameras[0].buffer_count = static_cast<std::uint32_t>(config.light_order.size() - 1);
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("buffer_count") != std::string::npos;
  if (!passed) {
    std::cerr << "camera buffer_count smaller than light_order was not rejected: "
              << error << "\n";
  }
  return passed;
}

bool test_lab_manual_trigger_config_validates() {
  auto config = make_filled_production_runtime_config();
  config.hardware_mode = seat_aoi::HardwareMode::Lab;
  config.signal.backend = seat_aoi::HardwareBackend::ManualTrigger;
  config.signal.station_id = "LAB_AOI_01";
  config.signal.default_sku = "seat_a_black_leather";
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  if (!ok) {
    std::cerr << "lab manual trigger config did not validate: " << error << "\n";
  }
  return ok;
}

bool test_production_rejects_manual_trigger_backend() {
  auto config = make_filled_production_runtime_config();
  config.signal.backend = seat_aoi::HardwareBackend::ManualTrigger;
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "production manual trigger was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_manual_trigger_signal_client_generates_trigger() {
  seat_aoi::ManualSignalClient client;
  seat_aoi::SignalClientConfig config;
  config.station_id = "LAB_AOI_01";
  config.default_sku = "seat_a_black_leather";
  if (!client.initialize(config)) {
    std::cerr << "manual trigger client did not initialize\n";
    return false;
  }
  seat_aoi::ExternalTrigger trigger;
  std::string error;
  const bool ok = client.wait_trigger(&trigger, 100, &error);
  const bool sent = client.publish_result(
      trigger, 1, seat_aoi::InspectionDecision::Recheck, 100, &error);
  const bool passed = ok && sent && trigger.trigger_id == 9000 &&
                      trigger.seat_id == "LAB_AOI_01_MANUAL_SEAT_9000" &&
                      trigger.sku == "seat_a_black_leather" &&
                      client.get_health().ok;
  if (!passed) {
    std::cerr << "manual trigger client produced unexpected trigger: "
              << trigger.trigger_id << " " << trigger.seat_id << " " << trigger.sku
              << " error=" << error << "\n";
  }
  return passed;
}

bool test_hikrobot_backend_fails_without_sdk_when_not_compiled() {
#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
  return true;
#else
  auto camera = seat_aoi::create_camera(seat_aoi::HardwareBackend::HikrobotMvs);
  seat_aoi::CameraConfig config;
  config.camera_id = "TOP_BACK";
  config.serial_number = "MV-CH120-20GC-TEST";
  config.calibration_id = "calib/top_back_production_v1";
  config.width = 4096;
  config.height = 3072;
  config.channels = 1;
  config.pixel_format = "Mono8";
  config.trigger_line = "Line0";
  config.exposure_output_line = "Line1";
  const bool initialized = camera->initialize(config);
  const auto health = camera->get_health();
  const bool passed = !initialized && !health.ok && !health.message.empty();
  if (!passed) {
    std::cerr << "hikrobot_mvs backend did not fail clearly without SDK: "
              << health.message << "\n";
  }
  return passed;
#endif
}

bool test_strobe_width_larger_than_exposure_rejected() {
  auto config = make_filled_production_runtime_config();
  config.light_channels[0].strobe_width_us = config.light_channels[0].exposure_us + 1U;
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "invalid strobe width was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_fl_acdh_timing_limits_rejected() {
  auto config = make_filled_production_runtime_config();
  config.light_channels[0].strobe_width_us = 9;
  std::string error;
  const bool low_strobe_ok = seat_aoi::validate_station_runtime_config(config, &error);

  config = make_filled_production_runtime_config();
  config.light_channels[0].strobe_width_us = 1000;
  error.clear();
  const bool high_strobe_ok = seat_aoi::validate_station_runtime_config(config, &error);

  config = make_filled_production_runtime_config();
  config.light_channels[0].trigger_delay_us = 4;
  error.clear();
  const bool low_delay_ok = seat_aoi::validate_station_runtime_config(config, &error);

  config = make_filled_production_runtime_config();
  config.light_channels[0].trigger_delay_us = 100;
  error.clear();
  const bool high_delay_ok = seat_aoi::validate_station_runtime_config(config, &error);

  const bool passed = !low_strobe_ok && !high_strobe_ok && !low_delay_ok && !high_delay_ok;
  if (!passed) {
    std::cerr << "FL-ACDH timing limits were not rejected\n";
  }
  return passed;
}

bool test_fl_acdh_strobe_width_uses_hex_payload() {
  const std::string value_100 = seat_aoi::FlAcdhLightController::format_strobe_width(100);
  const std::string value_500 = seat_aoi::FlAcdhLightController::format_strobe_width(500);
  const std::string value_999 = seat_aoi::FlAcdhLightController::format_strobe_width(999);
  const std::string frame_500 =
      seat_aoi::FlAcdhLightController::build_protocol_frame('9', '2', value_500);
  const bool passed = value_100 == "064" && value_500 == "1F4" &&
                      value_999 == "3E7" && frame_500 == "$921F46C";
  if (!passed) {
    std::cerr << "FL-ACDH strobe width was not encoded as 3-digit hex: "
              << value_100 << " " << value_500 << " " << value_999 << " "
              << frame_500 << "\n";
  }
  return passed;
}

bool test_fl_acdh_delay_uses_hex_payload() {
  const std::string value_10 = seat_aoi::FlAcdhLightController::format_delay(10);
  const std::string value_50 = seat_aoi::FlAcdhLightController::format_delay(50);
  const std::string value_99 = seat_aoi::FlAcdhLightController::format_delay(99);
  const std::string frame_99 =
      seat_aoi::FlAcdhLightController::build_protocol_frame('A', '1', value_99);
  const bool passed = value_10 == "00A" && value_50 == "032" &&
                      value_99 == "063" && frame_99 == "$A106361";
  if (!passed) {
    std::cerr << "FL-ACDH trigger delay was not encoded as 3-digit hex: "
              << value_10 << " " << value_50 << " " << value_99 << " "
              << frame_99 << "\n";
  }
  return passed;
}

bool test_invalid_health_threshold_rejected() {
  auto config = make_filled_production_runtime_config();
  config.warning_recheck_threshold = 3;
  config.critical_recheck_threshold = 3;
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("critical_recheck_threshold") != std::string::npos;
  if (!passed) {
    std::cerr << "invalid health threshold was not rejected: " << error << "\n";
  }
  return passed;
}

bool test_frame_slot_size_accounts_for_pixel_format_bytes() {
  seat_aoi::StationRuntimeConfig config;
  config.cameras = {
      seat_aoi::RuntimeCameraConfig{0, "TOP_BACK", "", "calib/simulated_v1", 64, 48, 1, "Mono16", "", "", 8, false},
      seat_aoi::RuntimeCameraConfig{1, "TOP_CUSHION", "", "calib/simulated_v1", 64, 48, 1, "Mono16", "", "", 8, false},
  };
  config.light_order = {1, 2, 3};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
      seat_aoi::RuntimeLightChannelConfig{0, 2, 2, 800, 800, 10, 1.0F, 60.0F},
      seat_aoi::RuntimeLightChannelConfig{0, 3, 3, 800, 800, 10, 1.0F, 55.0F},
  };
  config.frame_slot_size =
      static_cast<std::uint32_t>(seat_aoi::frame_slot_image_offset(6) + 64U * 48U * 6U);
  std::string error;
  const bool ok = seat_aoi::validate_station_runtime_config(config, &error);
  const bool passed = !ok && error.find("frame_slot_size") != std::string::npos;
  if (!passed) {
    std::cerr << "frame slot size did not account for Mono16 bytes: " << error << "\n";
  }
  return passed;
}

bool test_health_monitor_escalates_after_consecutive_rechecks() {
  seat_aoi::StationHealthMonitor health;
  health.configure(2, 3);
  health.transition_to(seat_aoi::StationState::Ready, "ready");
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::QualityFailed,
                       "quality 1");
  const auto warning = health.snapshot();
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::QualityFailed,
                       "quality 2");
  health.record_result(seat_aoi::InspectionDecision::Recheck,
                       seat_aoi::ErrorCode::QualityFailed,
                       "quality 3");
  const auto critical = health.snapshot();
  const bool passed = warning.alarm_level == seat_aoi::AlarmLevel::Warning &&
                      critical.alarm_level == seat_aoi::AlarmLevel::Critical &&
                      critical.state == seat_aoi::StationState::Fault &&
                      critical.consecutive_recheck_count == 3;
  if (!passed) {
    std::cerr << "health monitor did not escalate after consecutive rechecks\n";
  }
  return passed;
}

bool test_detector_timeout_fault_recovers_on_next_trigger() {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
  };
  const auto storage_root =
      std::filesystem::temp_directory_path() /
      ("seat_aoi_detector_timeout_" + std::to_string(seat_aoi::now_us()));
  config.trace_root = (storage_root / "trace").string();
  config.image_save.enabled = false;
  config.image_save.root_dir = (storage_root / "images").string();
  config.image_save.cleanup_enabled = false;
  config.image_save.cleanup_min_free_ratio = 0.0F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "detector timeout station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7011;
  trigger.seat_id = "SIM_TIMEOUT_BLOCK";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  const auto fault_snapshot = station.health_snapshot();
  seat_aoi::ExternalTrigger next_trigger;
  std::string error;
  const bool can_wait = station.wait_for_trigger(&next_trigger, &error);
  const auto recovered_snapshot = station.health_snapshot();
  station.cleanup_shared_memory();

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::DetectorTimeout &&
      fault_snapshot.state == seat_aoi::StationState::Fault &&
      can_wait &&
      recovered_snapshot.state == seat_aoi::StationState::Running &&
      next_trigger.trigger_id == 1000;
  if (!passed) {
    std::cerr << "detector timeout did not recover on next trigger: " << error << "\n";
  }
  return passed;
}

bool test_idle_trigger_wait_does_not_fault_station() {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.signal.backend = seat_aoi::HardwareBackend::ExternalSignal;
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
  };
  const auto storage_root =
      std::filesystem::temp_directory_path() /
      ("seat_aoi_idle_trigger_" + std::to_string(seat_aoi::now_us()));
  config.trace_root = (storage_root / "trace").string();
  config.signal.trigger_queue_path = (storage_root / "external_triggers.csv").string();
  config.image_save.enabled = false;
  config.image_save.root_dir = (storage_root / "images").string();
  config.image_save.cleanup_enabled = false;
  config.image_save.cleanup_min_free_ratio = 0.0F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "idle trigger station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  std::string error;
  const bool accepted = station.wait_for_trigger(&trigger, &error);
  const auto snapshot = station.health_snapshot();
  station.cleanup_shared_memory();

  const bool passed = !accepted && error.empty() &&
                      snapshot.state == seat_aoi::StationState::Ready &&
                      snapshot.alarm_level == seat_aoi::AlarmLevel::None &&
                      snapshot.total_jobs == 0 &&
                      snapshot.recheck_count == 0 &&
                      snapshot.device_fault_count == 0;
  if (!passed) {
    std::cerr << "idle trigger wait polluted station health: error=" << error
              << " state=" << seat_aoi::station_state_name(snapshot.state)
              << " alarm=" << seat_aoi::alarm_level_name(snapshot.alarm_level)
              << " total_jobs=" << snapshot.total_jobs
              << " recheck_count=" << snapshot.recheck_count
              << " device_fault_count=" << snapshot.device_fault_count << "\n";
  }
  return passed;
}

bool write_detector_result_slot(std::uint64_t sequence_id,
                                std::uint64_t trigger_id,
                                const std::string& seat_id,
                                std::uint32_t slot_count,
                                std::uint32_t result_slot_size,
                                seat_aoi::InspectionDecision decision,
                                std::uint32_t quality_pass,
                                seat_aoi::ErrorCode error_code,
                                std::uint32_t defect_count) {
  const std::size_t total_size =
      seat_aoi::shared_memory_total_size(slot_count, result_slot_size);
  const auto open_deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(300);
  seat_aoi::SharedMemory shm;
  while (std::chrono::steady_clock::now() < open_deadline) {
    if (shm.open_existing(seat_aoi::kResultShmName, total_size)) {
      break;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  if (!shm.is_open()) {
    std::cerr << "failed to open result shm for injected detector result\n";
    return false;
  }
  auto* base = slot_base(shm.data(), 0, result_slot_size);
  auto* slot = reinterpret_cast<seat_aoi::ResultSlotHeader*>(base);
  const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(200);
  while (std::chrono::steady_clock::now() < deadline) {
    std::uint32_t expected = static_cast<std::uint32_t>(seat_aoi::SlotState::Empty);
    if (slot->state.compare_exchange_strong(
            expected,
            static_cast<std::uint32_t>(seat_aoi::SlotState::Writing),
            std::memory_order_acq_rel)) {
      std::memset(base + sizeof(std::uint32_t),
                  0,
                  result_slot_size - sizeof(std::uint32_t));
      const std::size_t defect_bytes =
          static_cast<std::size_t>(defect_count) * sizeof(seat_aoi::DefectResultMeta);
      slot->sequence_id = sequence_id;
      slot->payload_size = seat_aoi::result_slot_defects_offset() + defect_bytes;
      slot->defect_count = defect_count;
      slot->reserved = 0;
      slot->result_meta.sequence_id = sequence_id;
      slot->result_meta.trigger_id = trigger_id;
      seat_aoi::copy_cstr(slot->result_meta.seat_id, seat_id);
      slot->result_meta.decision = static_cast<std::uint32_t>(decision);
      slot->result_meta.defect_count = defect_count;
      slot->result_meta.quality_pass = quality_pass;
      slot->result_meta.error_code = static_cast<std::uint32_t>(error_code);
      slot->result_meta.elapsed_ms = 1.0F;
      slot->result_meta.reserved = 0;
      slot->payload_crc32 =
          seat_aoi::crc32(base + seat_aoi::result_slot_defects_offset(), defect_bytes);
      slot->header_crc32 = 0;
      slot->header_crc32 = result_header_crc(slot);
      slot->state.store(static_cast<std::uint32_t>(seat_aoi::SlotState::Ready),
                        std::memory_order_release);
      shm.close();
      return true;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  shm.close();
  std::cerr << "result shm slot did not become empty for injected detector result\n";
  return false;
}

bool test_detector_ng_with_quality_failure_is_rechecked() {
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.publish_timeout_ms = 50;
  config.detector_timeout_ms = 500;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  const auto storage_root =
      std::filesystem::temp_directory_path() /
      ("seat_aoi_invalid_ng_" + std::to_string(seat_aoi::now_us()));
  config.trace_root = (storage_root / "trace").string();
  config.image_save.enabled = false;
  config.image_save.root_dir = (storage_root / "images").string();
  config.image_save.cleanup_enabled = false;
  config.image_save.cleanup_min_free_ratio = 0.0F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "invalid NG station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7012;
  trigger.seat_id = "SIM_INVALID_NG";
  trigger.sku = "seat_a_black_leather";
  const std::uint32_t slot_count = config.slot_count;
  const std::uint32_t result_slot_size = config.result_slot_size;
  std::thread detector_thread([&trigger, slot_count, result_slot_size]() {
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    (void)write_detector_result_slot(1,
                                     trigger.trigger_id,
                                     trigger.seat_id,
                                     slot_count,
                                     result_slot_size,
                                     seat_aoi::InspectionDecision::NG,
                                     0,
                                     seat_aoi::ErrorCode::QualityFailed,
                                     1);
  });
  const auto result = station.inspect_one_seat(trigger);
  detector_thread.join();
  station.cleanup_shared_memory();

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::InvalidPayload &&
      result.meta.quality_pass == 0;
  if (!passed) {
    std::cerr << "detector NG with quality failure was not converted to RECHECK: decision="
              << result.meta.decision << " error=" << result.meta.error_code << "\n";
  }
  return passed;
}

bool test_station_writes_detector_timeout_event_log() {
  const auto trace_root = std::filesystem::temp_directory_path() /
                          ("seat_aoi_cpp_event_log_test_" +
                           std::to_string(seat_aoi::now_us()));
  seat_aoi::StationConfig config;
  config.reset_shared_memory = true;
  config.slot_count = 1;
  config.frame_slot_size = 8192;
  config.result_slot_size = 4096;
  config.publish_timeout_ms = 5;
  config.detector_timeout_ms = 5;
  config.trigger_timeout_ms = 5;
  config.camera_timeout_ms = 5;
  config.light_timeout_ms = 5;
  config.recipe_id = "seat_a_black_leather_v1";
  config.light_order = {1};
  config.light_channels = {
      seat_aoi::RuntimeLightChannelConfig{0, 1, 1, 800, 800, 10, 1.0F, 60.0F},
  };
  config.trace_root = trace_root.string();
  config.image_save.enabled = false;
  config.image_save.root_dir = (trace_root / "images").string();
  config.image_save.cleanup_enabled = false;
  config.image_save.cleanup_min_free_ratio = 0.0F;

  seat_aoi::StationController station;
  if (!station.initialize(config)) {
    std::cerr << "event log station initialize failed\n";
    return false;
  }

  seat_aoi::ExternalTrigger trigger;
  trigger.trigger_id = 7010;
  trigger.seat_id = "SIM_TIMEOUT";
  trigger.sku = "seat_a_black_leather";
  const auto result = station.inspect_one_seat(trigger);
  station.cleanup_shared_memory();

  std::ifstream input(config.trace_root + "/cpp_controller_events.jsonl");
  std::string line;
  bool saw_timeout = false;
  while (std::getline(input, line)) {
    if (line.find("\"event\":\"inspection_recheck\"") != std::string::npos &&
        line.find("\"error\":\"DetectorTimeout\"") != std::string::npos &&
        line.find("\"trigger_id\":7010") != std::string::npos) {
      saw_timeout = true;
      break;
    }
  }

  const bool passed =
      static_cast<seat_aoi::InspectionDecision>(result.meta.decision) ==
          seat_aoi::InspectionDecision::Recheck &&
      static_cast<seat_aoi::ErrorCode>(result.meta.error_code) ==
          seat_aoi::ErrorCode::DetectorTimeout &&
      saw_timeout;
  if (!passed) {
    std::cerr << "detector timeout event log was not written\n";
  }
  return passed;
}

bool test_unsupported_signal_backend_fails_fast() {
  auto signal = seat_aoi::create_signal_client(seat_aoi::HardwareBackend::SerialAscii);
  seat_aoi::SignalClientConfig config;
  const bool ok = signal->initialize(config);
  const auto health = signal->get_health();
  const bool passed = !ok && !health.ok &&
                      health.message.find("backend=serial_ascii") != std::string::npos;
  if (!passed) {
    std::cerr << "unsupported signal backend did not fail fast: "
              << health.message << "\n";
    return false;
  }
  return true;
}

bool test_shared_memory_existing_size_mismatch_fails() {
  constexpr const char* kName = "/seat_aoi_shm_size_mismatch";
  seat_aoi::SharedMemory first;
  if (!first.create_or_open(kName, 4096, true)) {
    std::cerr << "initial shm create failed\n";
    return false;
  }
  seat_aoi::SharedMemory second;
  const bool ok = second.create_or_open(kName, 8192, false);
  second.close();
  first.unlink_name();
  const bool passed = !ok;
  if (!passed) {
    std::cerr << "existing shm size mismatch did not fail\n";
  }
  return passed;
}

bool test_invalid_bool_config_rejected() {
  const std::string path =
      (std::filesystem::temp_directory_path() /
       ("seat_aoi_invalid_bool_config_" + std::to_string(seat_aoi::now_us()) + ".conf"))
          .string();
  {
    std::ofstream output(path);
    output << "reset_shared_memory=flase\n";
  }
  seat_aoi::StationRuntimeConfig config;
  std::string error;
  const bool ok = seat_aoi::load_station_runtime_config(path, &config, &error);
  std::remove(path.c_str());
  const bool passed = !ok && error.find("reset_shared_memory") != std::string::npos;
  if (!passed) {
    std::cerr << "invalid bool config was not rejected: " << error << "\n";
  }
  return passed;
}

}  // namespace

int main() {
  if (!test_frame_ring_skips_blocked_slot()) {
    return 1;
  }
  if (!test_result_ring_returns_crc_error_immediately()) {
    return 1;
  }
  if (!test_result_ring_rejects_payload_size_mismatch()) {
    return 1;
  }
  if (!test_result_ring_reclaims_stale_and_bad_slots()) {
    return 1;
  }
  if (!test_result_ring_returns_current_bad_slot_error()) {
    return 1;
  }
  if (!test_ring_layout_mismatch_fails_without_reset()) {
    return 1;
  }
  if (!test_signal_trigger_timeout_fails_closed()) {
    return 1;
  }
  if (!test_tcp_signal_empty_terminator_combined_start_sn()) {
    return 1;
  }
  if (!test_tcp_signal_empty_terminator_splits_packed_start_sn()) {
    return 1;
  }
  if (!test_tcp_signal_empty_terminator_splits_quick_separate_start_sn()) {
    return 1;
  }
  if (!test_light_fault_returns_recheck()) {
    return 1;
  }
  if (!test_missing_frame_returns_recheck()) {
    return 1;
  }
  if (!test_frame_slot_unavailable_returns_recheck()) {
    return 1;
  }
  if (!test_runtime_light_channel_config_parses()) {
    return 1;
  }
  if (!test_runtime_multi_light_controller_config_rejected()) {
    return 1;
  }
  if (!test_replay_capture_config_parses()) {
    return 1;
  }
  if (!test_replay_config_rejected_outside_simulated_camera_backend()) {
    return 1;
  }
  if (!test_replay_camera_reads_selected_png_group()) {
    return 1;
  }
  if (!test_replay_camera_fails_when_light_group_missing()) {
    return 1;
  }
  if (!test_replay_camera_fails_when_selected_sample_is_incomplete()) {
    return 1;
  }
  if (!test_replay_camera_fails_on_shape_mismatch()) {
    return 1;
  }
  if (!test_replay_station_random_selects_complete_sample_pool()) {
    return 1;
  }
  if (!test_station_storage_failure_returns_recheck_before_capture()) {
    return 1;
  }
  if (!test_capture_only_bypasses_shared_memory_and_saves_images()) {
    return 1;
  }
  if (!test_invalid_light_controller_index_rejected()) {
    return 1;
  }
  if (!test_non_strobe_light_order_rejected()) {
    return 1;
  }
  if (!test_image_save_path_uses_date_directory()) {
    return 1;
  }
  if (!test_image_save_cleanup_removes_files_without_deleting_date_dirs()) {
    return 1;
  }
  if (!test_runtime_storage_cleanup_removes_trace_date_files()) {
    return 1;
  }
  if (!test_single_camera_config_validates()) {
    return 1;
  }
  if (!test_production_config_file_validates()) {
    return 1;
  }
  if (!test_filled_production_config_validates()) {
    return 1;
  }
  if (!test_camera_buffer_count_must_cover_light_order()) {
    return 1;
  }
  if (!test_lab_manual_trigger_config_validates()) {
    return 1;
  }
  if (!test_production_rejects_manual_trigger_backend()) {
    return 1;
  }
  if (!test_manual_trigger_signal_client_generates_trigger()) {
    return 1;
  }
  if (!test_hikrobot_backend_fails_without_sdk_when_not_compiled()) {
    return 1;
  }
  if (!test_strobe_width_larger_than_exposure_rejected()) {
    return 1;
  }
  if (!test_fl_acdh_timing_limits_rejected()) {
    return 1;
  }
  if (!test_fl_acdh_strobe_width_uses_hex_payload()) {
    return 1;
  }
  if (!test_fl_acdh_delay_uses_hex_payload()) {
    return 1;
  }
  if (!test_invalid_health_threshold_rejected()) {
    return 1;
  }
  if (!test_frame_slot_size_accounts_for_pixel_format_bytes()) {
    return 1;
  }
  if (!test_health_monitor_escalates_after_consecutive_rechecks()) {
    return 1;
  }
  if (!test_detector_timeout_fault_recovers_on_next_trigger()) {
    return 1;
  }
  if (!test_idle_trigger_wait_does_not_fault_station()) {
    return 1;
  }
  if (!test_detector_ng_with_quality_failure_is_rechecked()) {
    return 1;
  }
  if (!test_station_writes_detector_timeout_event_log()) {
    return 1;
  }
  if (!test_unsupported_signal_backend_fails_fast()) {
    return 1;
  }
  if (!test_shared_memory_existing_size_mismatch_fails()) {
    return 1;
  }
  if (!test_invalid_bool_config_rejected()) {
    return 1;
  }
  std::cout << "ipc safety checks passed\n";
  return 0;
}
