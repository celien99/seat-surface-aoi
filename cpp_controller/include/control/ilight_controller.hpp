#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

struct LightChannelParam {
  std::uint32_t controller_index = 0;  // 所属控制器索引（0-based）
  std::uint32_t light_index = 0;
  std::uint32_t physical_channel = 0;
  std::uint32_t exposure_us = 0;
  std::uint32_t strobe_width_us = 0;
  std::uint32_t trigger_delay_us = 0;
  float gain = 1.0F;
  float current_percent = 0.0F;
  bool enabled = true;                  // 单步跳过（对齐 Deploy lights[].enabled）
  std::uint32_t post_delay_ms = 50;     // 步骤后等待（对齐 Deploy lights[].post_delay_ms）
};

struct LightSequence {
  std::vector<LightChannelParam> channels;
};

struct LightControllerConfig {
  std::string device_id;
  std::string host;
  std::uint32_t port = 0;
  std::string serial_port;
  std::uint32_t baud_rate = 0;
  std::string trigger_input_line;
  bool simulate_fault = false;
};

struct LightHealth {
  bool ok = true;
  bool ready = true;
  bool over_current = false;
  bool over_temperature = false;
  bool trigger_missed = false;
  std::uint64_t trigger_count = 0;
  std::uint32_t last_light_index = 0;
  std::uint32_t last_physical_channel = 0;
  std::string message = "simulated";
};

/// 光源控制器抽象接口。
/// trigger_channel 对齐 Deploy 设计：每个光源步骤 = 完整配置+点火序列。
class ILightController {
public:
  virtual ~ILightController() = default;
  virtual bool initialize(const LightControllerConfig& config) = 0;
  virtual bool prepare_sequence(const LightSequence& sequence,
                                std::uint64_t trigger_id,
                                int timeout_ms,
                                std::string* error_message) = 0;
  /// 完整频闪序列：配置（C→B→8→9→A）→ 点火（7）→ 等待响应。
  /// 对应 Deploy 的每个光源步骤。
  virtual bool trigger_channel(const LightChannelParam& channel,
                               std::uint64_t trigger_id,
                               std::uint32_t light_seq_index,
                               int timeout_ms,
                               std::string* error_message) = 0;
  virtual LightHealth get_health() const = 0;
  virtual void shutdown_all() = 0;
};

}  // namespace seat_aoi
