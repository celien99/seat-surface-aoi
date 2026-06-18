#pragma once

#include <cstdint>
#include <string>

#include "control/ilight_controller.hpp"

namespace seat_aoi {

/// FL-ACDH-20048-4 频闪光源控制器，通过 RS232 串口通信。
///
/// 协议：$[cmd][ch][val_3chars][checksum_2hex]\r\n
/// 校验和：从 $ 到 value 末字节的 XOR，格式化为 2 位大写十六进制。
/// 响应：$ = 接受, & = 拒绝（C/B 命令可忽略拒绝，其余关键命令必须报错）
class FlAcdhLightController final : public ILightController {
public:
  FlAcdhLightController() = default;
  FlAcdhLightController(const FlAcdhLightController&) = delete;
  FlAcdhLightController& operator=(const FlAcdhLightController&) = delete;
  ~FlAcdhLightController() override;

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

  bool arm_hardware_trigger(const LightChannelParam& channel,
                            std::uint64_t trigger_id,
                            std::uint32_t light_seq_index,
                            int timeout_ms,
                            std::string* error_message) override;

  bool notify_hardware_triggered(const LightChannelParam& channel,
                                 std::uint64_t trigger_id,
                                 std::uint32_t light_seq_index,
                                 int timeout_ms,
                                 std::string* error_message) override;

  bool run_sequence(const LightSequence& sequence,
                    std::uint64_t trigger_id,
                    int timeout_ms,
                    std::string* error_message = nullptr) override;

  bool set_channel(std::uint32_t light_index, const LightChannelParam& param) override;

  LightHealth get_health() const override;

  void shutdown_all() override;

private:
  void close();

  /// 构建 FL-ACDH 协议帧（不含 \r\n）
  static std::string build_frame(char cmd, char channel, const std::string& value);

  /// 计算 XOR 校验和并格式化为 2 位大写十六进制
  static std::string compute_checksum(const std::string& payload);

  /// 发送帧并读取单字节响应，返回 true 表示 $
  bool send_frame(const std::string& frame, int timeout_ms, std::string* error_message);

  /// 发送命令：allow_rejection=true 时 & 只记录日志不报错
  bool send_command(char cmd, char channel, const std::string& value,
                    bool allow_rejection, int timeout_ms, std::string* error_message);

  /// 发送一条命令的完整序列（C→B→8→9→A→7），用于 trigger_channel
  bool send_full_sequence(const LightChannelParam& channel,
                           std::uint64_t trigger_id,
                           std::uint32_t light_seq_index,
                           int timeout_ms,
                           std::string* error_message);

  /// 发送 arm 序列（C→B→8→9→A），用于 arm_hardware_trigger
  bool send_arm_sequence(const LightChannelParam& channel,
                          std::uint64_t trigger_id,
                          std::uint32_t light_seq_index,
                          int timeout_ms,
                          std::string* error_message);

  /// 发送触发命令（7），用于 notify_hardware_triggered
  bool send_trigger_command(const LightChannelParam& channel,
                             std::uint64_t trigger_id,
                             std::uint32_t light_seq_index,
                             int timeout_ms,
                             std::string* error_message);

  /// 将 strobe_width_us 格式化为 3 位十进制字符串
  static std::string format_strobe_width(std::uint32_t strobe_width_us);

  /// 将 trigger_delay_us 格式化为 3 位十进制字符串
  static std::string format_delay(std::uint32_t trigger_delay_us);

  /// 将 physical_channel 转为 ASCII 通道字符
  static char channel_char(std::uint32_t physical_channel);

  // ---- 平台相关串口操作 ----
  bool open_serial(const std::string& port, std::uint32_t baud_rate,
                   std::string* error_message);
  void close_serial();
  int write_serial(const void* data, std::size_t size, int timeout_ms);
  int read_serial(void* buffer, std::size_t size, int timeout_ms);

  // ---- 状态 ----
  bool initialized_ = false;
  bool simulate_fault_ = false;
  std::string serial_port_;
  std::uint32_t baud_rate_ = 9600;

#ifdef _WIN32
  void* handle_ = nullptr;
#else
  int fd_ = -1;
#endif

  std::uint64_t trigger_count_ = 0;
  std::uint32_t last_light_index_ = 0;
  std::uint32_t last_physical_channel_ = 0;
  bool hardware_trigger_armed_ = false;
  LightChannelParam armed_channel_{};
};

}  // namespace seat_aoi
