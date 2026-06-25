#include "control/tcp_signal_client.hpp"

#include <chrono>
#include <cstring>
#include <iostream>
#include <sstream>
#include <string>
#include <thread>

#include "common/string_utils.hpp"

#ifdef _WIN32
#include <winsock2.h>
#include <ws2tcpip.h>
#pragma comment(lib, "ws2_32.lib")
#else
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <poll.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <unistd.h>
#endif

namespace seat_aoi {

namespace {

#ifdef _WIN32
int close_socket_impl(TcpSignalClient::socket_t sock) {
  return ::closesocket(static_cast<SOCKET>(sock));
}

bool init_winsock(std::string* error_message) {
  WSADATA data{};
  const int ret = WSAStartup(MAKEWORD(2, 2), &data);
  if (ret != 0) {
    if (error_message != nullptr) {
      std::ostringstream oss;
      oss << "TCP WSAStartup failed (err=" << ret << ")";
      *error_message = oss.str();
    }
    return false;
  }
  return true;
}

#else
int close_socket_impl(TcpSignalClient::socket_t sock) {
  return ::close(sock);
}
#endif

}  // namespace

// ============================================================================
// 析构
// ============================================================================

TcpSignalClient::~TcpSignalClient() {
  close();
}

// ============================================================================
// 平台相关 socket 操作
// ============================================================================

#ifdef _WIN32
void TcpSignalClient::close_socket() {
  if (client_sock_ != kInvalidSocket) {
    close_socket_impl(client_sock_);
    client_sock_ = kInvalidSocket;
  }
  if (listen_sock_ != kInvalidSocket) {
    close_socket_impl(listen_sock_);
    listen_sock_ = kInvalidSocket;
  }
}

bool TcpSignalClient::set_socket_timeout(int timeout_ms) {
  if (client_sock_ == kInvalidSocket) {
    return false;
  }
  const DWORD tv = static_cast<DWORD>(timeout_ms);
  ::setsockopt(static_cast<SOCKET>(client_sock_), SOL_SOCKET, SO_RCVTIMEO,
               reinterpret_cast<const char*>(&tv), sizeof(tv));
  ::setsockopt(static_cast<SOCKET>(client_sock_), SOL_SOCKET, SO_SNDTIMEO,
               reinterpret_cast<const char*>(&tv), sizeof(tv));
  return true;
}

int TcpSignalClient::read_socket(void* buffer, std::size_t size,
                                  int timeout_ms) {
  if (client_sock_ == kInvalidSocket) {
    return -1;
  }
  fd_set readfds;
  FD_ZERO(&readfds);
  FD_SET(static_cast<SOCKET>(client_sock_), &readfds);
  struct timeval tv{};
  tv.tv_sec = timeout_ms / 1000;
  tv.tv_usec = (timeout_ms % 1000) * 1000;
  const int select_ret = ::select(0, &readfds, nullptr, nullptr, &tv);
  if (select_ret <= 0) {
    return 0;
  }
  const int n = ::recv(static_cast<SOCKET>(client_sock_),
                       static_cast<char*>(buffer),
                       static_cast<int>(size), 0);
  return n;
}

#else  // POSIX

void TcpSignalClient::close_socket() {
  if (client_sock_ != kInvalidSocket) {
    close_socket_impl(client_sock_);
    client_sock_ = kInvalidSocket;
  }
  if (listen_sock_ != kInvalidSocket) {
    close_socket_impl(listen_sock_);
    listen_sock_ = kInvalidSocket;
  }
}

bool TcpSignalClient::set_socket_timeout(int timeout_ms) {
  if (client_sock_ == kInvalidSocket) {
    return false;
  }
  struct timeval tv{};
  tv.tv_sec = timeout_ms / 1000;
  tv.tv_usec = (timeout_ms % 1000) * 1000;
  ::setsockopt(client_sock_, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
  ::setsockopt(client_sock_, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
  return true;
}

int TcpSignalClient::read_socket(void* buffer, std::size_t size,
                                  int timeout_ms) {
  if (client_sock_ == kInvalidSocket) {
    return -1;
  }
  struct pollfd pfd{};
  pfd.fd = client_sock_;
  pfd.events = POLLIN;
  const int poll_ret = ::poll(&pfd, 1, timeout_ms);
  if (poll_ret <= 0) {
    return 0;  // 超时
  }
  const ssize_t n = ::recv(client_sock_, buffer, size, 0);
  if (n < 0) {
    return -1;
  }
  return static_cast<int>(n);
}

#endif

// ============================================================================
// TCP 监听与连接
// ============================================================================

bool TcpSignalClient::start_listen(int port, std::string* error_message) {
  close_socket();

#ifdef _WIN32
  if (!init_winsock(error_message)) {
    return false;
  }
  socket_t sock = static_cast<socket_t>(::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
#else
  socket_t sock = ::socket(AF_INET, SOCK_STREAM, 0);
#endif
  if (sock == kInvalidSocket) {
    if (error_message != nullptr) {
      *error_message = "TCP socket 创建失败";
    }
    return false;
  }

  // 允许地址重用
  int opt = 1;
  ::setsockopt(static_cast<int>(sock), SOL_SOCKET, SO_REUSEADDR,
#ifdef _WIN32
               reinterpret_cast<const char*>(&opt),
#else
               &opt,
#endif
               sizeof(opt));

  struct sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_addr.s_addr = INADDR_ANY;
  addr.sin_port = htons(static_cast<uint16_t>(port));

  if (::bind(static_cast<int>(sock),
             reinterpret_cast<struct sockaddr*>(&addr),
             sizeof(addr)) != 0) {
    close_socket_impl(sock);
    if (error_message != nullptr) {
      std::ostringstream oss;
      oss << "TCP bind 端口 " << port << " 失败";
      *error_message = oss.str();
    }
    return false;
  }

  if (::listen(static_cast<int>(sock), 1) != 0) {
    close_socket_impl(sock);
    if (error_message != nullptr) {
      *error_message = "TCP listen 失败";
    }
    return false;
  }

  listen_sock_ = sock;
  std::cout << "TCP 信号服务端 端口 " << port << " 已启动监听" << std::endl;
  return true;
}

bool TcpSignalClient::accept_client(int timeout_ms,
                                     std::string* error_message) {
  if (listen_sock_ == kInvalidSocket) {
    if (error_message != nullptr) {
      *error_message = "TCP 监听未启动";
    }
    return false;
  }

  // 使用 poll/select 等待客户端连接
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
#ifdef _WIN32
    fd_set readfds;
    FD_ZERO(&readfds);
    FD_SET(static_cast<SOCKET>(listen_sock_), &readfds);
    struct timeval tv{};
    const auto remaining_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now())
            .count();
    tv.tv_sec = static_cast<long>(remaining_ms / 1000);
    tv.tv_usec = static_cast<long>((remaining_ms % 1000) * 1000);
    if (tv.tv_sec < 0) tv.tv_sec = 0;
    if (tv.tv_usec < 0) tv.tv_usec = 0;
    const int poll_ret = ::select(0, &readfds, nullptr, nullptr, &tv);
#else
    struct pollfd pfd{};
    pfd.fd = listen_sock_;
    pfd.events = POLLIN;
    const auto remaining_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now())
            .count();
    const int poll_ret = ::poll(&pfd, 1,
                                remaining_ms < 0 ? 0 : static_cast<int>(remaining_ms));
#endif
    if (poll_ret < 0) {
      break;
    }
    if (poll_ret == 0) {
      continue;
    }

    struct sockaddr_in client_addr{};
#ifdef _WIN32
    int addr_len = sizeof(client_addr);
#else
    socklen_t addr_len = sizeof(client_addr);
#endif
    socket_t client =
#ifdef _WIN32
        static_cast<socket_t>(::accept(static_cast<SOCKET>(listen_sock_),
                                        reinterpret_cast<struct sockaddr*>(&client_addr),
                                        &addr_len));
#else
        ::accept(listen_sock_,
                 reinterpret_cast<struct sockaddr*>(&client_addr),
                 &addr_len);
#endif
    if (client == kInvalidSocket) {
      break;
    }

    // 关闭旧连接
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
    }
    client_sock_ = client;

    char ip_str[INET_ADDRSTRLEN]{};
    inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, sizeof(ip_str));
    std::cout << "TCP 信号客户端已连接: " << ip_str << ":"
              << ntohs(client_addr.sin_port) << std::endl;
    return true;
  }

  if (error_message != nullptr) {
    *error_message = "TCP 等待客户端连接超时";
  }
  return false;
}

// ============================================================================
// 行读取
// ============================================================================

bool TcpSignalClient::read_line(std::string* line, int timeout_ms,
                                 std::string* error_message) {
  if (client_sock_ == kInvalidSocket) {
    if (error_message != nullptr) {
      *error_message = "TCP 客户端未连接";
    }
    return false;
  }

  set_socket_timeout(timeout_ms);

  line->clear();
  char ch = 0;
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);

  while (std::chrono::steady_clock::now() < deadline) {
    const auto remaining_ms =
        std::chrono::duration_cast<std::chrono::milliseconds>(
            deadline - std::chrono::steady_clock::now())
            .count();
    if (remaining_ms <= 0) {
      break;
    }
    const int n = read_socket(&ch, 1, static_cast<int>(remaining_ms));
    if (n <= 0) {
      // 超时或断连
      break;
    }
    line->push_back(ch);
    // 检查 terminator
    if (line->size() >= terminator_.size() &&
        line->compare(line->size() - terminator_.size(),
                       terminator_.size(), terminator_) == 0) {
      return true;
    }
  }

  if (line->empty()) {
    if (error_message != nullptr) {
      *error_message = "TCP 读取超时或连接断开";
    }
    return false;
  }
  // 即使没收到完整的 terminator，也返回已读取的数据
  return true;
}

// ============================================================================
// 触发行解析
// ============================================================================

bool TcpSignalClient::parse_trigger_line(const std::string& line,
                                          ExternalTrigger* out_trigger,
                                          std::string* error_message) {
  const std::string raw = trim(line);
  if (raw.empty()) {
    if (error_message != nullptr) {
      *error_message = "TCP 收到空行";
    }
    return false;
  }

  std::string sn;
  if (delimiter_.empty()) {
    // 裸 SN 模式：整行即为 SN
    sn = raw;
  } else {
    // 分隔符模式：delimiter|SN
    const auto pos = raw.find(delimiter_);
    if (pos == std::string::npos) {
      if (error_message != nullptr) {
        *error_message = "TCP 触发行不含分隔符 '" + delimiter_ + "': " + raw;
      }
      return false;
    }
    sn = raw.substr(pos + delimiter_.size());
    sn = trim(sn);
  }

  if (sn.empty()) {
    if (error_message != nullptr) {
      *error_message = "TCP 收到空 SN";
    }
    return false;
  }

  ExternalTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = station_id_ + "_" + sn;
  trigger.sku = default_sku_;
  *out_trigger = trigger;

  std::cout << "TCP 收到 SN: " << sn
            << " seat_id=" << trigger.seat_id
            << " trigger_id=" << trigger.trigger_id << std::endl;
  return true;
}

// ============================================================================
// 回复
// ============================================================================

void TcpSignalClient::send_ok() {
  send_response(ok_response_);
}

void TcpSignalClient::send_response(const std::string& response) {
  if (client_sock_ == kInvalidSocket) {
    return;
  }
  ::send(static_cast<int>(client_sock_), response.data(),
         response.size(), 0);
}

// ============================================================================
// ISignalClient 接口实现
// ============================================================================

bool TcpSignalClient::initialize(const SignalClientConfig& config) {
  close();

  port_ = config.port > 0 ? config.port : 9000;
  delimiter_ = config.delimiter;
  terminator_ = config.terminator.empty() ? "\n" : config.terminator;
  ok_response_ = config.ok_response.empty() ? "ok\n" : config.ok_response;
  protocol_mode_ = config.protocol_mode.empty() ? "single" : config.protocol_mode;
  start_command_ = config.start_command.empty() ? "start" : config.start_command;
  sn_prefix_ = config.sn_prefix.empty() ? "sn" : config.sn_prefix;
  start_ack_ = config.start_ack.empty() ? "start_ack\n" : config.start_ack;
  sn_ack_ = config.sn_ack.empty() ? "sn_ack\n" : config.sn_ack;
  result_host_ = config.result_host;
  result_port_ = config.result_port;
  result_prefix_ = config.result_prefix.empty() ? "result" : config.result_prefix;
  result_delimiter_ = config.result_delimiter.empty() ? "|" : config.result_delimiter;
  ok_text_ = config.ok_text.empty() ? "OK" : config.ok_text;
  ng_text_ = config.ng_text.empty() ? "NG" : config.ng_text;
  recheck_text_ = config.recheck_text.empty() ? "RECHECK" : config.recheck_text;
  error_text_ = config.error_text.empty() ? "ERROR" : config.error_text;
  station_id_ = config.station_id.empty() ? "TCP_AOI" : config.station_id;
  default_seat_id_ = config.default_seat_id.empty() ? "EXTERNAL_SEAT"
                                                     : config.default_seat_id;
  default_sku_ = config.default_sku.empty() ? "seat_a_black_leather"
                                             : config.default_sku;
  simulate_output_fault_ = config.simulate_output_fault;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  next_trigger_id_ = 1;

  std::string listen_error;
  if (!start_listen(static_cast<int>(port_), &listen_error)) {
    std::cerr << listen_error << std::endl;
    return false;
  }

  initialized_ = true;
  return true;
}

bool TcpSignalClient::wait_trigger(ExternalTrigger* out_trigger,
                                    int timeout_ms,
                                    std::string* error_message) {
  if (!initialized_ || out_trigger == nullptr || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "TCP 信号客户端未初始化、输出指针为空或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    std::this_thread::sleep_for(std::chrono::milliseconds(timeout_ms));
    if (error_message != nullptr) {
      *error_message = "TCP 模拟触发超时";
    }
    return false;
  }

  // 如果没有客户端连接，等待连接
  if (client_sock_ == kInvalidSocket) {
    std::string accept_error;
    if (!accept_client(timeout_ms, &accept_error)) {
      if (error_message != nullptr) {
        *error_message = accept_error;
      }
      return false;
    }
  }

  // 按协议模式分派
  if (protocol_mode_ == "start_sn") {
    return wait_trigger_start_sn(out_trigger, timeout_ms, error_message);
  }

  // 单行协议路径 (protocol_mode=single, 向后兼容)
  // 读取一行
  std::string line;
  if (!read_line(&line, timeout_ms, error_message)) {
    // 读取失败时关闭客户端，下次重试
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  // 解析触发行
  ExternalTrigger trigger{};
  std::string parse_error;
  if (!parse_trigger_line(line, &trigger, &parse_error)) {
    if (error_message != nullptr) {
      *error_message = parse_error;
    }
    return false;
  }

  // 回复 ok
  send_ok();

  *out_trigger = trigger;
  return true;
}

bool TcpSignalClient::wait_trigger_start_sn(ExternalTrigger* out_trigger,
                                             int timeout_ms,
                                             std::string* error_message) {
  const auto deadline = std::chrono::steady_clock::now() +
                        std::chrono::milliseconds(timeout_ms);

  // 步骤 1: 等待到位信号 (start_command)
  auto remaining_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
      deadline - std::chrono::steady_clock::now()).count();
  if (remaining_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "TCP 两步协议等待到位信号 (" + start_command_ + ") 超时";
    }
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  std::string start_line;
  if (!read_line(&start_line, static_cast<int>(remaining_ms), error_message)) {
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  const std::string start_trimmed = trim(start_line);
  if (start_trimmed != start_command_) {
    if (error_message != nullptr) {
      *error_message = "TCP 两步协议期望到位信号 '" + start_command_ +
                       "', 实际收到: " + start_trimmed;
    }
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  // 回复到位确认
  send_response(start_ack_);
  std::cout << "TCP 两步协议: 收到到位信号 (" << start_command_
            << "), 已回复确认" << std::endl;

  // 步骤 2: 等待 SN 条码
  remaining_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
      deadline - std::chrono::steady_clock::now()).count();
  if (remaining_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "TCP 两步协议等待 SN 条码超时";
    }
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  std::string sn_line;
  if (!read_line(&sn_line, static_cast<int>(remaining_ms), error_message)) {
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  // 解析 "sn_prefix <barcode>" 格式
  const std::string sn_trimmed = trim(sn_line);
  const std::string expected_prefix = sn_prefix_ + " ";
  if (sn_trimmed.size() <= expected_prefix.size() ||
      sn_trimmed.compare(0, expected_prefix.size(), expected_prefix) != 0) {
    if (error_message != nullptr) {
      *error_message = "TCP 两步协议期望 '" + expected_prefix +
                       "<barcode>', 实际收到: " + sn_trimmed;
    }
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  std::string barcode = trim(sn_trimmed.substr(expected_prefix.size()));
  if (barcode.empty()) {
    if (error_message != nullptr) {
      *error_message = "TCP 两步协议: SN 条码为空";
    }
    if (client_sock_ != kInvalidSocket) {
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
    }
    return false;
  }

  // 回复 SN 确认
  send_response(sn_ack_);

  // 构造 ExternalTrigger
  ExternalTrigger trigger{};
  trigger.trigger_id = next_trigger_id_++;
  trigger.seat_id = station_id_ + "_" + barcode;
  trigger.sku = default_sku_;
  *out_trigger = trigger;

  std::cout << "TCP 两步协议: 收到 SN=" << barcode
            << " seat_id=" << trigger.seat_id
            << " trigger_id=" << trigger.trigger_id << std::endl;
  return true;
}

bool TcpSignalClient::publish_result(const ExternalTrigger& trigger,
                                      std::uint64_t sequence_id,
                                      InspectionDecision decision,
                                      int timeout_ms,
                                      std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0) {
    if (error_message != nullptr) {
      *error_message = "TCP 信号客户端未初始化或 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_output_fault_) {
    if (error_message != nullptr) {
      *error_message = "TCP 模拟结果发布失败";
    }
    return false;
  }

  std::string decision_text;
  switch (decision) {
    case InspectionDecision::OK:      decision_text = ok_text_;      break;
    case InspectionDecision::NG:      decision_text = ng_text_;      break;
    case InspectionDecision::Recheck: decision_text = recheck_text_; break;
    case InspectionDecision::Error:   decision_text = error_text_;   break;
  }

  const std::string seat_id(trigger.seat_id);
  std::cout << "TCP 信号 sequence_id=" << sequence_id
            << " trigger_id=" << trigger.trigger_id
            << " seat_id=" << seat_id
            << " decision=" << decision_text << std::endl;

  // 尝试通过 result_host:result_port 发送结果
  std::string send_error;
  if (!result_host_.empty() && result_port_ > 0) {
    if (send_result_line(seat_id, decision_text, timeout_ms, &send_error)) {
      return true;
    }
    std::cerr << "TCP result notify failed: " << send_error
              << " (will not block main flow)" << std::endl;
  }

  // 回退：尝试通过已有 PLC 连接发送
  if (client_sock_ != kInvalidSocket) {
    const std::string line = result_prefix_ + result_delimiter_ +
                             seat_id + result_delimiter_ +
                             decision_text + terminator_;
    set_socket_timeout(timeout_ms);
    const int sent = static_cast<int>(
        ::send(static_cast<int>(client_sock_), line.data(), line.size(), 0));
    if (sent == static_cast<int>(line.size())) {
      return true;
    }
  }

  // 无有效 TCP 通道时仅日志，不报错
  return true;
}

TcpSignalClient::socket_t TcpSignalClient::connect_to(const std::string& host,
                                                       std::uint32_t port,
                                                       std::string* error_message) {
#ifdef _WIN32
  socket_t sock = static_cast<socket_t>(::socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
#else
  socket_t sock = ::socket(AF_INET, SOCK_STREAM, 0);
#endif
  if (sock == kInvalidSocket) {
    if (error_message != nullptr) *error_message = "result notify socket 创建失败";
    return kInvalidSocket;
  }

  struct sockaddr_in addr{};
  addr.sin_family = AF_INET;
  addr.sin_port = htons(static_cast<uint16_t>(port));
  if (inet_pton(AF_INET, host.c_str(), &addr.sin_addr) != 1) {
    close_socket_impl(sock);
    if (error_message != nullptr)
      *error_message = "result notify host 解析失败: " + host;
    return kInvalidSocket;
  }

  if (::connect(static_cast<int>(sock),
                reinterpret_cast<struct sockaddr*>(&addr),
                sizeof(addr)) != 0) {
    close_socket_impl(sock);
    if (error_message != nullptr)
      *error_message = "result notify 连接失败 " + host + ":" + std::to_string(port);
    return kInvalidSocket;
  }
  return sock;
}

bool TcpSignalClient::send_result_line(const std::string& seat_id,
                                        const std::string& decision_text,
                                        int timeout_ms,
                                        std::string* error_message) {
  socket_t sock = connect_to(result_host_, result_port_, error_message);
  if (sock == kInvalidSocket) return false;

  const std::string line = result_prefix_ + result_delimiter_ +
                           seat_id + result_delimiter_ +
                           decision_text + terminator_;

  // 设置发送超时
#ifdef _WIN32
  const DWORD tv = static_cast<DWORD>(timeout_ms);
  ::setsockopt(static_cast<SOCKET>(sock), SOL_SOCKET, SO_SNDTIMEO,
               reinterpret_cast<const char*>(&tv), sizeof(tv));
#else
  struct timeval tv{};
  tv.tv_sec = timeout_ms / 1000;
  tv.tv_usec = (timeout_ms % 1000) * 1000;
  ::setsockopt(sock, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
#endif

  const int sent = static_cast<int>(
      ::send(static_cast<int>(sock), line.data(), line.size(), 0));
  close_socket_impl(sock);
  if (sent != static_cast<int>(line.size())) {
    if (error_message != nullptr)
      *error_message = "result notify 发送失败";
    return false;
  }
  return true;
}

SignalHealth TcpSignalClient::get_health() const {
  SignalHealth health;
  health.ok = initialized_ && !simulate_output_fault_ && !simulate_trigger_timeout_ &&
              listen_sock_ != kInvalidSocket;
  if (!initialized_) {
    health.message = "TCP 信号未初始化";
  } else if (simulate_trigger_timeout_) {
    health.message = "TCP 模拟触发超时";
  } else if (simulate_output_fault_) {
    health.message = "TCP 模拟结果发布失败";
  } else {
    std::ostringstream oss;
    oss << "TCP 信号服务端 端口 " << port_
        << (client_sock_ != kInvalidSocket ? " (已连接)" : " (等待连接)");
    health.message = oss.str();
  }
  return health;
}

void TcpSignalClient::close() {
  initialized_ = false;
  close_socket();
}

}  // namespace seat_aoi
