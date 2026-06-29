#pragma once

#include <chrono>
#include <cstdint>
#include <string>

#include "control/isignal_client.hpp"

namespace seat_aoi {

/// TCP 信号客户端，监听指定端口接收 PLC/上位机发送的 SN 触发行。
///
/// 支持两种协议模式（与 Deploy 项目 PLC 协议一致）：
/// 1. 裸 SN 模式（delimiter=""）：接收 SN\n，回复 ok\n
/// 2. 分隔符模式（delimiter 非空）：接收 start|SN\n，回复 ok\n
///
/// C++ 作为 TCP 服务端被动监听，PLC 作为客户端主动连接。
class TcpSignalClient final : public ISignalClient {
public:
#ifdef _WIN32
  using socket_t = unsigned long long;  // SOCKET
  static constexpr socket_t kInvalidSocket =
      static_cast<socket_t>(~static_cast<unsigned int>(0));
#else
  using socket_t = int;
  static constexpr socket_t kInvalidSocket = -1;
#endif

  TcpSignalClient() = default;
  TcpSignalClient(const TcpSignalClient&) = delete;
  TcpSignalClient& operator=(const TcpSignalClient&) = delete;
  ~TcpSignalClient() override;

  bool initialize(const SignalClientConfig& config) override;

  bool wait_trigger(ExternalTrigger* out_trigger,
                    int timeout_ms,
                    std::string* error_message) override;

  bool publish_result(const ExternalTrigger& trigger,
                      std::uint64_t sequence_id,
                      InspectionDecision decision,
                      int timeout_ms,
                      std::string* error_message) override;

  SignalHealth get_health() const override;

private:
  void close();

  /// 启动 TCP 监听
  bool start_listen(int port, std::string* error_message);
  /// 接受一个客户端连接（带超时）
  bool accept_client(int timeout_ms, std::string* error_message);
  /// 从客户端读取一行（直到 terminator）
  bool read_line(std::string* line, int timeout_ms, std::string* error_message);
  /// 从 TCP 接收缓存中提取一条已成帧消息。
  bool try_extract_buffered_line(std::string* line);
  /// 解析触发行 → ExternalTrigger
  bool parse_trigger_line(const std::string& line,
                          ExternalTrigger* out_trigger,
                          std::string* error_message);
  /// 校验现场 SN，避免粘包或控制字符进入 seat_id。
  bool validate_barcode(const std::string& barcode,
                        std::string* error_message) const;
  /// 发送 ok_response 给客户端
  void send_ok();
  /// 发送自定义回复文本
  void send_response(const std::string& response);
  /// start_sn 两步协议: 等待 start 命令 → ack → 等待 sn <barcode> → ack
  bool wait_trigger_start_sn(ExternalTrigger* out_trigger,
                              int timeout_ms,
                              std::string* error_message);
  /// 通过 TCP 发送结果回传行
  bool send_result_line(const std::string& seat_id, const std::string& decision_text,
                        int timeout_ms, std::string* error_message);

  /// 平台相关 socket 操作
  void close_socket();
  bool set_socket_timeout(int timeout_ms);
  int read_socket(void* buffer, std::size_t size, int timeout_ms);
  socket_t connect_to(const std::string& host, std::uint32_t port,
                      int timeout_ms, std::string* error_message);

  bool initialized_ = false;
  bool simulate_output_fault_ = false;
  bool simulate_trigger_timeout_ = false;

  std::uint32_t port_ = 9000;
  std::string delimiter_;
  std::string terminator_ = "\n";
  std::string ok_response_ = "ok\n";
  // 协议模式 "single" / "start_sn"
  std::string protocol_mode_ = "single";
  // start_sn 两步协议配置
  std::string start_command_ = "start";
  std::string sn_prefix_ = "sn";
  std::string start_ack_ = "start_ack\n";
  std::string sn_ack_ = "sn_ack\n";
  std::string station_id_;
  std::string default_seat_id_ = "EXTERNAL_SEAT";
  std::string default_sku_ = "seat_a_black_leather";

  // TCP 结果回传
  std::string result_host_;
  std::uint32_t result_port_ = 0;
  std::string result_prefix_ = "result";
  std::string result_delimiter_ = "|";
  std::string ok_text_ = "OK";
  std::string ng_text_ = "NG";
  std::string recheck_text_ = "RECHECK";
  std::string error_text_ = "ERROR";

  std::uint64_t next_trigger_id_ = 1;

  socket_t listen_sock_ = kInvalidSocket;
  socket_t client_sock_ = kInvalidSocket;
  std::string pending_rx_;
  std::chrono::steady_clock::time_point pending_rx_updated_at_{};
};

}  // namespace seat_aoi
