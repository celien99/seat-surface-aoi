#include "ipc/result_ring_buffer.hpp"

#include <chrono>
#include <array>
#include <cstring>
#include <sstream>
#include <thread>

#include "common/time_utils.hpp"
#include "ipc/crc32.hpp"

namespace seat_aoi {

namespace {

void initialize_header(ShmHeader* header, std::uint32_t slot_count, std::uint32_t slot_size) {
  header->magic = kShmProtocolMagic;
  header->version = kShmProtocolVersion;
  header->slot_count = slot_count;
  header->slot_size = slot_size;
  header->write_index = 0;
  header->read_index = 0;
  header->heartbeat = now_us();
}

std::uint32_t result_header_crc(const ResultSlotHeader* slot) {
  std::array<std::uint8_t, sizeof(ResultSlotHeader)> bytes{};
  std::memcpy(bytes.data(), slot, bytes.size());
  std::memset(bytes.data(), 0, sizeof(std::uint32_t));
  std::memset(bytes.data() + 20, 0, sizeof(std::uint32_t));
  return crc32(bytes.data(), bytes.size());
}

}  // namespace

bool ResultRingBuffer::initialize(const std::string& name,
                                  std::uint32_t slot_count,
                                  std::uint32_t slot_size,
                                  bool reset) {
  slot_count_ = slot_count;
  slot_size_ = slot_size;
  const std::size_t total_size = shared_memory_total_size(slot_count, slot_size);
  if (!shm_.create_or_open(name, total_size, reset)) {
    return false;
  }

  auto* h = header();
  if (reset || h->magic != kShmProtocolMagic || h->version != kShmProtocolVersion ||
      h->slot_count != slot_count || h->slot_size != slot_size) {
    std::memset(shm_.data(), 0, total_size);
    initialize_header(h, slot_count, slot_size);
    for (std::uint32_t i = 0; i < slot_count; ++i) {
      slot_header(i)->state.store(static_cast<std::uint32_t>(SlotState::Empty),
                                  std::memory_order_release);
    }
  }
  return true;
}

bool ResultRingBuffer::wait_for_result(std::uint64_t sequence_id,
                                       int timeout_ms,
                                       InspectionResultPayload* out_result,
                                       ErrorCode* out_error_code,
                                       std::string* error_message) {
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);
  if (out_error_code != nullptr) {
    *out_error_code = ErrorCode::None;
  }

  while (std::chrono::steady_clock::now() < deadline) {
    for (std::uint32_t i = 0; i < slot_count_; ++i) {
      ErrorCode slot_error_code = ErrorCode::None;
      if (read_ready_slot(i, sequence_id, out_result, &slot_error_code, error_message)) {
        header()->read_index += 1;
        header()->heartbeat = now_us();
        return true;
      }
      if (slot_error_code != ErrorCode::None) {
        if (out_error_code != nullptr) {
          *out_error_code = slot_error_code;
        }
        header()->read_index += 1;
        header()->heartbeat = now_us();
        return false;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }

  if (out_error_code != nullptr) {
    *out_error_code = ErrorCode::DetectorTimeout;
  }
  if (error_message != nullptr) {
    *error_message = "detector result timeout";
  }
  return false;
}

void ResultRingBuffer::close() {
  shm_.close();
}

void ResultRingBuffer::unlink_name() {
  shm_.unlink_name();
}

ShmHeader* ResultRingBuffer::header() {
  return reinterpret_cast<ShmHeader*>(shm_.data());
}

ResultSlotHeader* ResultRingBuffer::slot_header(std::uint32_t slot_index) {
  return reinterpret_cast<ResultSlotHeader*>(slot_base(slot_index));
}

std::uint8_t* ResultRingBuffer::slot_base(std::uint32_t slot_index) {
  auto* base = static_cast<std::uint8_t*>(shm_.data());
  return base + sizeof(ShmHeader) + static_cast<std::size_t>(slot_index) * slot_size_;
}

bool ResultRingBuffer::read_ready_slot(std::uint32_t slot_index,
                                       std::uint64_t sequence_id,
                                       InspectionResultPayload* out_result,
                                       ErrorCode* out_error_code,
                                       std::string* error_message) {
  auto* slot = slot_header(slot_index);
  const auto state = static_cast<SlotState>(slot->state.load(std::memory_order_acquire));
  if (state != SlotState::Ready || slot->sequence_id != sequence_id) {
    return false;
  }

  slot->state.store(static_cast<std::uint32_t>(SlotState::Reading), std::memory_order_release);
  if (slot->payload_size < result_slot_defects_offset() ||
      slot->payload_size > slot_size_ ||
      slot->defect_count > kMaxDefectsPerResult) {
    slot->state.store(static_cast<std::uint32_t>(SlotState::Empty),
                      std::memory_order_release);
    if (out_error_code != nullptr) {
      *out_error_code = ErrorCode::InvalidPayload;
    }
    if (error_message != nullptr) {
      *error_message = "invalid result payload size or defect count";
    }
    return false;
  }

  const std::uint32_t expected_payload_crc =
      crc32(slot_base(slot_index) + result_slot_defects_offset(),
            slot->payload_size - result_slot_defects_offset());
  if (expected_payload_crc != slot->payload_crc32) {
    slot->state.store(static_cast<std::uint32_t>(SlotState::Empty),
                      std::memory_order_release);
    if (out_error_code != nullptr) {
      *out_error_code = ErrorCode::CrcMismatch;
    }
    if (error_message != nullptr) {
      *error_message = "result payload CRC mismatch";
    }
    return false;
  }

  if (result_header_crc(slot) != slot->header_crc32) {
    slot->state.store(static_cast<std::uint32_t>(SlotState::Empty),
                      std::memory_order_release);
    if (out_error_code != nullptr) {
      *out_error_code = ErrorCode::CrcMismatch;
    }
    if (error_message != nullptr) {
      *error_message = "result header CRC mismatch";
    }
    return false;
  }

  out_result->meta = slot->result_meta;
  out_result->defects.clear();
  out_result->defects.resize(slot->defect_count);
  if (slot->defect_count > 0) {
    const auto* defects = reinterpret_cast<const DefectResultMeta*>(
        slot_base(slot_index) + result_slot_defects_offset());
    std::memcpy(out_result->defects.data(), defects,
                slot->defect_count * sizeof(DefectResultMeta));
  }

  slot->state.store(static_cast<std::uint32_t>(SlotState::Empty), std::memory_order_release);
  return true;
}

}  // namespace seat_aoi
