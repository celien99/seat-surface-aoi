#pragma once

#include <cstdint>
#include <string>

#include "control/ilight_controller.hpp"

namespace seat_aoi {

/// FL-ACDH-20048-4 频闪光源控制器，通过 RS232 串口通信。
class FlAcdhLightController final : public ILightController {
public:
  FlAcdhLightController() = default;
  FlAcdhLightController(const FlAcdhLightController&) = delete;
  FlAcdhLightController& operator=(const FlAcdhLightController&) = delete;
  ~FlAcdhLightController() override;

  /// 构造一条 FL-ACDH 原始协议帧，供协议回归测试直接校验。
  static std::string build_protocol_frame(char cmd, char channel, const std::string& value);
  /// 格式化 9 命令频闪脉宽；控制器要求 3 位十六进制数据。
  static std::string format_strobe_width(std::uint32_t strobe_width_us);

  bool initialize(const LightControllerConfig& config) override;
  bool prepare_sequence(const LightSequence& sequence,
                        std::uint64_t trigger_id,
                        int timeout_ms,
                        std::string* error_message) override;
  bool trigger_channel(const LightChannelParam& channel,
                       std::uint64_t trigger_id,
                       std::uint32_t light_seq_index,
                       int timeout_ms,
                       std::string* error_message) override;
  LightHealth get_health() const override;
  void shutdown_all() override;

private:
  void close();

  /// 发送一条 FL-ACDH 命令
  bool send_command(char cmd, char channel, const std::string& value,
                    int timeout_ms, std::string* error_message);

  // ---- 协议工具 ----
  static std::string compute_checksum(const std::string& payload);
  static char channel_char(std::uint32_t physical_channel);
  static std::string format_delay(std::uint32_t trigger_delay_us);
  bool send_frame(const std::string& frame, int timeout_ms, std::string* error_message);

  // ---- 平台相关串口 ----
  bool open_serial(const std::string& port, std::uint32_t baud_rate, std::string* error_message);
  void close_serial();
  int write_serial(const void* data, std::size_t size, int timeout_ms);
  int read_serial(void* buffer, std::size_t size, int timeout_ms);

  bool initialized_ = false;
  bool simulate_fault_ = false;
  std::string serial_port_;
  std::uint32_t baud_rate_ = 9600;
  LightSerialResponseMode response_mode_ = LightSerialResponseMode::Ack;

#ifdef _WIN32
  void* handle_ = nullptr;
#else
  int fd_ = -1;
#endif

  std::uint64_t trigger_count_ = 0;
  std::uint32_t last_light_index_ = 0;
  std::uint32_t last_physical_channel_ = 0;
};

}  // namespace seat_aoi
