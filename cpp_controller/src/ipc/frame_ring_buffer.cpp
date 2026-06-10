#include "ipc/frame_ring_buffer.hpp"

#include <algorithm>
#include <array>
#include <chrono>
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

std::uint32_t frame_header_crc(const FrameSlotHeader* slot) {
  std::array<std::uint8_t, sizeof(FrameSlotHeader)> bytes{};
  std::memcpy(bytes.data(), slot, bytes.size());
  std::memset(bytes.data(), 0, sizeof(std::uint32_t));
  std::memset(bytes.data() + 20, 0, sizeof(std::uint32_t));
  return crc32(bytes.data(), bytes.size());
}

}  // namespace

bool FrameRingBuffer::initialize(const std::string& name,
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

bool FrameRingBuffer::publish(const SeatImageBundle& bundle,
                              int timeout_ms,
                              std::uint64_t* out_sequence_id,
                              std::string* error_message) {
  std::size_t payload_size = 0;
  if (!validate_bundle(bundle, &payload_size, error_message)) {
    return false;
  }

  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);

  while (std::chrono::steady_clock::now() < deadline) {
    auto* h = header();
    const std::uint64_t start_index = h->write_index;
    for (std::uint32_t probe = 0; probe < slot_count_; ++probe) {
      const std::uint32_t slot_index =
          static_cast<std::uint32_t>((start_index + probe) % slot_count_);
      auto* slot = slot_header(slot_index);
      std::uint32_t expected = static_cast<std::uint32_t>(SlotState::Empty);
      if (slot->state.compare_exchange_strong(expected,
                                              static_cast<std::uint32_t>(SlotState::Writing),
                                              std::memory_order_acq_rel)) {
        auto* base = slot_base(slot_index);
        std::memset(base + sizeof(std::uint32_t), 0, slot_size_ - sizeof(std::uint32_t));

        const std::uint32_t frame_count = static_cast<std::uint32_t>(bundle.frames.size());
        std::vector<LightFrameMeta> metas;
        metas.reserve(bundle.frames.size());

        std::uint64_t image_offset = frame_slot_image_offset(frame_count);
        for (const auto& frame : bundle.frames) {
          auto meta = frame.meta;
          meta.image_offset = image_offset;
          meta.image_size = frame.bytes.size();
          meta.image_crc32 = crc32(frame.bytes.data(), frame.bytes.size());
          metas.push_back(meta);
          std::memcpy(base + image_offset, frame.bytes.data(), frame.bytes.size());
          image_offset += frame.bytes.size();
        }

        slot->sequence_id = bundle.job_meta.sequence_id;
        slot->payload_size = payload_size;
        slot->frame_meta_count = frame_count;
        slot->reserved = 0;
        slot->job_meta = bundle.job_meta;
        auto* meta_dst = reinterpret_cast<LightFrameMeta*>(base + frame_slot_meta_offset());
        std::memcpy(meta_dst, metas.data(), metas.size() * sizeof(LightFrameMeta));
        slot->payload_crc32 =
            crc32(base + frame_slot_meta_offset(), payload_size - frame_slot_meta_offset());
        slot->header_crc32 = 0;
        slot->header_crc32 = frame_header_crc(slot);
        slot->state.store(static_cast<std::uint32_t>(SlotState::Ready),
                          std::memory_order_release);
        h->write_index = start_index + probe + 1;
        h->heartbeat = now_us();
        if (out_sequence_id != nullptr) {
          *out_sequence_id = bundle.job_meta.sequence_id;
        }
        return true;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }

  if (error_message != nullptr) {
    *error_message = "frame slot unavailable before timeout";
  }
  return false;
}

void FrameRingBuffer::close() {
  shm_.close();
}

void FrameRingBuffer::unlink_name() {
  shm_.unlink_name();
}

ShmHeader* FrameRingBuffer::header() {
  return reinterpret_cast<ShmHeader*>(shm_.data());
}

FrameSlotHeader* FrameRingBuffer::slot_header(std::uint32_t slot_index) {
  return reinterpret_cast<FrameSlotHeader*>(slot_base(slot_index));
}

std::uint8_t* FrameRingBuffer::slot_base(std::uint32_t slot_index) {
  auto* base = static_cast<std::uint8_t*>(shm_.data());
  return base + sizeof(ShmHeader) + static_cast<std::size_t>(slot_index) * slot_size_;
}

bool FrameRingBuffer::validate_bundle(const SeatImageBundle& bundle,
                                      std::size_t* payload_size,
                                      std::string* error_message) const {
  if (bundle.frames.empty()) {
    if (error_message != nullptr) {
      *error_message = "bundle contains no frames";
    }
    return false;
  }
  if (bundle.frames.size() > kMaxFramesPerJob) {
    if (error_message != nullptr) {
      *error_message = "bundle exceeds max frame count";
    }
    return false;
  }
  if (bundle.job_meta.frame_count != bundle.frames.size()) {
    if (error_message != nullptr) {
      *error_message = "job_meta.frame_count does not match frames";
    }
    return false;
  }

  std::size_t size = frame_slot_image_offset(static_cast<std::uint32_t>(bundle.frames.size()));
  for (const auto& frame : bundle.frames) {
    if (frame.bytes.empty()) {
      if (error_message != nullptr) {
        *error_message = "frame has empty payload";
      }
      return false;
    }
    size += frame.bytes.size();
  }

  if (size > slot_size_) {
    std::ostringstream oss;
    oss << "payload size " << size << " exceeds slot size " << slot_size_;
    if (error_message != nullptr) {
      *error_message = oss.str();
    }
    return false;
  }
  *payload_size = size;
  return true;
}

}  // namespace seat_aoi
