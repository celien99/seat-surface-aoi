#include "control/tcp_signal_client.hpp"

#include <algorithm>
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

constexpr int kReadTimeout = -2;
constexpr int kInterByteTimeoutMs = 100;
constexpr std::size_t kMaxBarcodeLength = 48;

bool is_barcode_char(char ch) {
  return (ch >= '0' && ch <= '9') ||
         (ch >= 'A' && ch <= 'Z') ||
         (ch >= 'a' && ch <= 'z') ||
         ch == '_' || ch == '-' || ch == '.';
}

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
  pending_rx_.clear();
  pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
  awaiting_sn_ = false;
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
  if (select_ret == 0) {
    return kReadTimeout;
  }
  if (select_ret < 0) {
    return -1;
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
  pending_rx_.clear();
  pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
  awaiting_sn_ = false;
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
  if (poll_ret == 0) {
    return kReadTimeout;
  }
  if (poll_ret < 0) {
    return -1;
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

  // timeout_ms <= 0 → 无限等待，用固定短周期轮询以避免空转
  const bool infinite = (timeout_ms <= 0);
  constexpr int kAcceptPollMs = 200;

  // 使用 poll/select 等待客户端连接
  const auto deadline = infinite
      ? std::chrono::steady_clock::time_point::max()
      : std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    int poll_ms;
    if (infinite) {
      poll_ms = kAcceptPollMs;
    } else {
      const auto remaining =
          std::chrono::duration_cast<std::chrono::milliseconds>(
              deadline - std::chrono::steady_clock::now()).count();
      if (remaining <= 0) break;
      poll_ms = static_cast<int>(remaining);
    }

#ifdef _WIN32
    fd_set readfds;
    FD_ZERO(&readfds);
    FD_SET(static_cast<SOCKET>(listen_sock_), &readfds);
    struct timeval tv{};
    tv.tv_sec = poll_ms / 1000;
    tv.tv_usec = (poll_ms % 1000) * 1000;
    if (tv.tv_sec < 0) { tv.tv_sec = 0; tv.tv_usec = 0; }
    const int poll_ret = ::select(0, &readfds, nullptr, nullptr, &tv);
#else
    struct pollfd pfd{};
    pfd.fd = listen_sock_;
    pfd.events = POLLIN;
    const int poll_ret = ::poll(&pfd, 1, poll_ms);
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
    pending_rx_.clear();
    pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
    awaiting_sn_ = false;

    char ip_str[INET_ADDRSTRLEN]{};
    inet_ntop(AF_INET, &client_addr.sin_addr, ip_str, sizeof(ip_str));
    std::cout << "TCP 信号客户端已连接: " << ip_str << ":"
              << ntohs(client_addr.sin_port) << std::endl;
    return true;
  }

  if (error_message != nullptr) {
    if (infinite) {
      *error_message = "TCP 等待客户端连接超时";
    } else {
      error_message->clear();
    }
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

  constexpr int kInternalPollMs = 200;
  const bool infinite = (timeout_ms <= 0);
  const int socket_timeout = infinite ? kInternalPollMs : timeout_ms;
  set_socket_timeout(socket_timeout);

  line->clear();
  bool timed_out = false;
  bool disconnected = false;

  // 无限模式不设截止时间，仅通过连接断开 / 错误退出
  const auto deadline = infinite
      ? std::chrono::steady_clock::time_point::max()
      : std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);

  if (try_extract_buffered_line(line)) {
    return true;
  }
  if (terminator_.empty() && !pending_rx_.empty() &&
      pending_rx_updated_at_ != std::chrono::steady_clock::time_point{} &&
      std::chrono::steady_clock::now() - pending_rx_updated_at_ >=
          std::chrono::milliseconds(kInterByteTimeoutMs)) {
    line->swap(pending_rx_);
    pending_rx_.clear();
    pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
    return true;
  }

  while (std::chrono::steady_clock::now() < deadline) {
    int read_timeout;
    if (infinite) {
      read_timeout = kInternalPollMs;
    } else {
      const auto remaining =
          std::chrono::duration_cast<std::chrono::milliseconds>(
              deadline - std::chrono::steady_clock::now()).count();
      if (remaining <= 0) break;
      read_timeout = static_cast<int>(remaining);
    }
    if (terminator_.empty() && !pending_rx_.empty()) {
      read_timeout = std::min(read_timeout, kInterByteTimeoutMs);
    }

    char buffer[256]{};
    const int n = read_socket(buffer, sizeof(buffer), read_timeout);
    if (n == kReadTimeout) {
      if (terminator_.empty() && !pending_rx_.empty()) {
        line->swap(pending_rx_);
        pending_rx_.clear();
        pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
        return true;
      }
      if (!infinite) {
        timed_out = true;
        break;
      }
      continue;  // 无限模式：超时无数据 → 继续等
    }
    if (n == 0) {
      if (!pending_rx_.empty()) {
        line->swap(pending_rx_);
        pending_rx_.clear();
        pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
        return true;
      }
      disconnected = true;
      break;
    }
    if (n < 0) {
      break;
    }
    pending_rx_.append(buffer, static_cast<std::size_t>(n));
    pending_rx_updated_at_ = std::chrono::steady_clock::now();

    if (try_extract_buffered_line(line)) {
      return true;
    }
  }

  if (!pending_rx_.empty()) {
    line->swap(pending_rx_);
    pending_rx_.clear();
    pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
    return true;
  }

  if (line->empty()) {
    if (error_message != nullptr) {
      if (timed_out) {
        if (infinite) {
          *error_message = "TCP 等待触发行超时";
        } else {
          error_message->clear();
        }
      } else if (disconnected) {
        *error_message = "TCP 客户端连接断开";
      } else {
        *error_message = "TCP 读取失败";
      }
    }
    return false;
  }
  // 即使没收到完整的 terminator，也返回已读取的数据
  return true;
}

bool TcpSignalClient::try_extract_buffered_line(std::string* line) {
  if (line == nullptr || pending_rx_.empty()) {
    return false;
  }

  if (!terminator_.empty()) {
    const auto pos = pending_rx_.find(terminator_);
    if (pos == std::string::npos) {
      return false;
    }
    const auto end = pos + terminator_.size();
    line->assign(pending_rx_.data(), end);
    pending_rx_.erase(0, end);
    if (pending_rx_.empty()) {
      pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
    }
    return true;
  }

  if (protocol_mode_ == "start_sn" && !delimiter_.empty()) {
    const std::string combined_prefix = start_command_ + delimiter_;
    const auto first = pending_rx_.find(combined_prefix);
    if (first != std::string::npos) {
      if (first > 0) {
        line->assign(pending_rx_.data(), first);
        pending_rx_.erase(0, first);
        if (pending_rx_.empty()) {
          pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
        }
        return true;
      }

      const auto second = pending_rx_.find(combined_prefix, combined_prefix.size());
      if (second != std::string::npos) {
        line->assign(pending_rx_.data(), second);
        pending_rx_.erase(0, second);
        if (pending_rx_.empty()) {
          pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
        }
        return true;
      }
    }
  }

  return false;
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
  if (!validate_barcode(sn, error_message)) {
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

bool TcpSignalClient::validate_barcode(const std::string& barcode,
                                        std::string* error_message) const {
  if (barcode.empty()) {
    if (error_message != nullptr) {
      *error_message = "TCP 收到空 SN";
    }
    return false;
  }
  if (barcode.size() > kMaxBarcodeLength) {
    if (error_message != nullptr) {
      *error_message = "TCP SN 长度超过 " + std::to_string(kMaxBarcodeLength) +
                       " 个字符: " + barcode;
    }
    return false;
  }
  const bool valid = std::all_of(barcode.begin(), barcode.end(), is_barcode_char);
  if (!valid) {
    if (error_message != nullptr) {
      *error_message = "TCP SN 只能包含字母、数字、横线、下划线或点: " + barcode;
    }
    return false;
  }
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

bool TcpSignalClient::wait_presence_gate(std::string* error_message) {
  int distance_mm = 0;
  if (!presence_gate_.wait_until_present(&distance_mm, error_message)) {
    return false;
  }
  if (presence_gate_.enabled()) {
    std::cout << "JK-LRD 位移到位 distance_mm=" << distance_mm << std::endl;
  }
  return true;
}

// ============================================================================
// ISignalClient 接口实现
// ============================================================================

bool TcpSignalClient::initialize(const SignalClientConfig& config) {
  close();

  port_ = config.port > 0 ? config.port : 9000;
  delimiter_ = config.delimiter;
  // 空 terminator 是生产配置支持的有效模式：外部信号不带换行时，
  // read_line() 通过字节间短超时判断一条 TCP 消息结束。
  terminator_ = config.terminator;
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
  publish_results_on_command_channel_ = config.publish_results_on_command_channel;
  station_id_ = config.station_id.empty() ? "TCP_AOI" : config.station_id;
  default_seat_id_ = config.default_seat_id.empty() ? "EXTERNAL_SEAT"
                                                     : config.default_seat_id;
  default_sku_ = config.default_sku.empty() ? "seat_a_black_leather"
                                             : config.default_sku;
  simulate_output_fault_ = config.simulate_output_fault;
  simulate_trigger_timeout_ = config.simulate_trigger_timeout;
  next_trigger_id_ = 1;
  awaiting_sn_ = false;

  std::string gate_error;
  if (!presence_gate_.initialize(config.jklrd_gate, &gate_error)) {
    std::cerr << gate_error << std::endl;
    return false;
  }

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
  if (!initialized_ || out_trigger == nullptr) {
    if (error_message != nullptr) {
      *error_message = "TCP 信号客户端未初始化或输出指针为空";
    }
    return false;
  }
  if (timeout_ms < 0) {
    if (error_message != nullptr) {
      *error_message = "TCP 信号客户端 timeout_ms 非法";
    }
    return false;
  }
  if (simulate_trigger_timeout_) {
    const int wait_ms = timeout_ms > 0 ? timeout_ms : 1000;
    std::this_thread::sleep_for(std::chrono::milliseconds(wait_ms));
    if (error_message != nullptr) {
      *error_message = "TCP 模拟触发超时";
    }
    return false;
  }

  // timeout_ms == 0 → 无限等待，不设截止时间
  const bool infinite = (timeout_ms <= 0);

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

  // 单行协议路径 — 循环读取直到收到有效触发行
  while (true) {
    std::string line;
    if (!read_line(&line, timeout_ms, error_message)) {
      // read_line 超时在无限模式下不会发生；连接断开则关闭 socket 并重试
      if (infinite && (error_message == nullptr ||
                       *error_message == "TCP 客户端连接断开" ||
                       *error_message == "TCP 客户端未连接")) {
        close_socket_impl(client_sock_);
        client_sock_ = kInvalidSocket;
        pending_rx_.clear();
        pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
        std::string accept_error;
        if (!accept_client(timeout_ms, &accept_error)) {
          if (error_message != nullptr) {
            *error_message = accept_error;
          }
          return false;
        }
        continue;
      }
      // 有限超时模式下保留 socket 供下次复用
      if (!infinite && error_message != nullptr && error_message->empty()) {
        return false;
      }
      close_socket_impl(client_sock_);
      client_sock_ = kInvalidSocket;
      pending_rx_.clear();
      pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
      return false;
    }

    // 解析触发行
    ExternalTrigger trigger{};
    std::string parse_error;
    if (parse_trigger_line(line, &trigger, &parse_error)) {
      if (!wait_presence_gate(error_message)) {
        return false;
      }
      send_ok();
      *out_trigger = trigger;
      return true;
    }
    // 解析失败 → 忽略非法行，继续等待下一条
    std::cerr << "TCP 忽略非法触发行: " << parse_error << std::endl;
  }
}

bool TcpSignalClient::wait_trigger_start_sn(ExternalTrigger* out_trigger,
                                             int timeout_ms,
                                             std::string* error_message) {
  const bool infinite = (timeout_ms <= 0);

  if (!awaiting_sn_) {
    // 步骤 1: 等待到位信号 (start_command)
    // 循环忽略不匹配的行，直到收到 start_command 或连接断开
    while (true) {
      std::string line;
      if (!read_line(&line, timeout_ms, error_message)) {
        if (!infinite && error_message != nullptr && error_message->empty()) {
          return false;
        }
        // 无限模式：read_line 只在连接断开/错误时返回 false
        if (client_sock_ != kInvalidSocket) {
          close_socket_impl(client_sock_);
          client_sock_ = kInvalidSocket;
          pending_rx_.clear();
          pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
          awaiting_sn_ = false;
        }
        return false;
      }

      const std::string trimmed = trim(line);

      // 组合格式检测: start_command + delimiter + SN（单行触发）
      // 仅在 delimiter 非空时启用，向后兼容旧两步协议
      if (!delimiter_.empty()) {
        const std::string combined_prefix = start_command_ + delimiter_;
        if (trimmed.size() > combined_prefix.size() &&
            trimmed.compare(0, combined_prefix.size(), combined_prefix) == 0) {
          std::string barcode = trim(trimmed.substr(combined_prefix.size()));
          if (barcode.empty()) {
            std::cerr << "TCP 两步协议: 收到组合格式触发行但 SN 为空 \""
                      << trimmed << "\"" << std::endl;
            continue;
          }
          std::string barcode_error;
          if (!validate_barcode(barcode, &barcode_error)) {
            std::cerr << "TCP 两步协议: 忽略非法组合格式 SN: "
                      << barcode_error << std::endl;
            continue;
          }
          if (!wait_presence_gate(error_message)) {
            return false;
          }
          // 组合格式已包含到位信号和 SN，直接回复 SN 确认
          send_response(sn_ack_);

          ExternalTrigger trigger{};
          trigger.trigger_id = next_trigger_id_++;
          trigger.seat_id = station_id_ + "_" + barcode;
          trigger.sku = default_sku_;
          *out_trigger = trigger;

          std::cout << "TCP 两步协议: 收到组合格式触发 start_command+delimiter+SN="
                    << barcode << " seat_id=" << trigger.seat_id
                    << " trigger_id=" << trigger.trigger_id << std::endl;
          return true;
        }
      }

      if (trimmed == start_command_) {
        break;  // 收到到位信号
      }
      // 非到位信号的行 → 忽略，继续等待
      std::cerr << "TCP 两步协议: 忽略非到位信号行 \"" << trimmed << "\"" << std::endl;
    }

    // 回复到位确认
    send_response(start_ack_);
    awaiting_sn_ = true;
    std::cout << "TCP 两步协议: 收到到位信号 (" << start_command_
              << "), 已回复确认" << std::endl;
    if (!infinite) {
      if (error_message != nullptr) {
        error_message->clear();
      }
      return false;
    }
  }

  // 步骤 2: 等待 SN 条码
  // 循环忽略非法格式，直到收到合法 SN 或连接断开
  while (true) {
    std::string sn_line;
    if (!read_line(&sn_line, timeout_ms, error_message)) {
      if (!infinite && error_message != nullptr && error_message->empty()) {
        return false;
      }
      if (client_sock_ != kInvalidSocket) {
        close_socket_impl(client_sock_);
        client_sock_ = kInvalidSocket;
        pending_rx_.clear();
        pending_rx_updated_at_ = std::chrono::steady_clock::time_point{};
        awaiting_sn_ = false;
      }
      if (error_message != nullptr && *error_message == "TCP 等待触发行超时") {
        if (infinite) {
          *error_message = "TCP 两步协议等待 SN 条码超时";
        } else {
          error_message->clear();
        }
      }
      return false;
    }

    const std::string sn_trimmed = trim(sn_line);
    const std::string expected_prefix = sn_prefix_ + " ";
    if (sn_trimmed.size() <= expected_prefix.size() ||
        sn_trimmed.compare(0, expected_prefix.size(), expected_prefix) != 0) {
      std::cerr << "TCP 两步协议: 忽略非法 SN 行 \"" << sn_trimmed << "\"" << std::endl;
      continue;
    }

    std::string barcode = trim(sn_trimmed.substr(expected_prefix.size()));
    if (barcode.empty()) {
      std::cerr << "TCP 两步协议: 忽略空 SN" << std::endl;
      continue;
    }
    std::string barcode_error;
    if (!validate_barcode(barcode, &barcode_error)) {
      std::cerr << "TCP 两步协议: 忽略非法 SN: "
                << barcode_error << std::endl;
      continue;
    }
    if (!wait_presence_gate(error_message)) {
      return false;
    }

    // 回复 SN 确认
    send_response(sn_ack_);
    awaiting_sn_ = false;

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
    // 已配置独立结果通道但不可达时，不再回退到 PLC 命令通道，
    // 避免污染 start_sn 协议状态机导致后续触发失败。
    return true;
  }

  // 回退：无独立结果通道时，尝试通过已有 PLC 连接发送
  if (publish_results_on_command_channel_ && client_sock_ != kInvalidSocket) {
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
                                                       int timeout_ms,
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

  // 非阻塞 connect + select/poll 实现真实超时
#ifdef _WIN32
  u_long nonblock = 1;
  ::ioctlsocket(static_cast<SOCKET>(sock), FIONBIO, &nonblock);
#else
  int flags = ::fcntl(sock, F_GETFL, 0);
  ::fcntl(sock, F_SETFL, flags | O_NONBLOCK);
#endif

  int connect_ret =
#ifdef _WIN32
      ::connect(static_cast<SOCKET>(sock),
                reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr));
#else
      ::connect(sock, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr));
#endif

  if (connect_ret != 0) {
#ifdef _WIN32
    if (WSAGetLastError() != WSAEWOULDBLOCK)
#else
    if (errno != EINPROGRESS)
#endif
    {
      close_socket_impl(sock);
      if (error_message != nullptr)
        *error_message = "result notify 连接失败 " + host + ":" + std::to_string(port);
      return kInvalidSocket;
    }

    // 等待 socket 变为可写（连接完成或失败）
    const int effective_timeout = timeout_ms > 0 ? timeout_ms : 200;
#ifdef _WIN32
    fd_set writefds;
    FD_ZERO(&writefds);
    FD_SET(static_cast<SOCKET>(sock), &writefds);
    struct timeval tv{};
    tv.tv_sec = effective_timeout / 1000;
    tv.tv_usec = (effective_timeout % 1000) * 1000;
    const int sel_ret = ::select(0, nullptr, &writefds, nullptr, &tv);
#else
    struct pollfd pfd{};
    pfd.fd = sock;
    pfd.events = POLLOUT;
    const int sel_ret = ::poll(&pfd, 1, effective_timeout);
#endif
    if (sel_ret <= 0) {
      close_socket_impl(sock);
      if (error_message != nullptr)
        *error_message = "result notify 连接超时 " + host + ":" + std::to_string(port);
      return kInvalidSocket;
    }

    // 检查 socket 错误状态确认连接是否真的成功
    int sock_err = 0;
#ifdef _WIN32
    int sock_err_len = sizeof(sock_err);
    ::getsockopt(static_cast<SOCKET>(sock), SOL_SOCKET, SO_ERROR,
                 reinterpret_cast<char*>(&sock_err), &sock_err_len);
#else
    socklen_t sock_err_len = sizeof(sock_err);
    ::getsockopt(sock, SOL_SOCKET, SO_ERROR, &sock_err, &sock_err_len);
#endif
    if (sock_err != 0) {
      close_socket_impl(sock);
      if (error_message != nullptr)
        *error_message = "result notify 连接失败 " + host + ":" + std::to_string(port);
      return kInvalidSocket;
    }
  }

  // 恢复为阻塞模式
#ifdef _WIN32
  nonblock = 0;
  ::ioctlsocket(static_cast<SOCKET>(sock), FIONBIO, &nonblock);
#else
  ::fcntl(sock, F_SETFL, flags);
#endif

  return sock;
}

bool TcpSignalClient::send_result_line(const std::string& seat_id,
                                        const std::string& decision_text,
                                        int timeout_ms,
                                        std::string* error_message) {
  socket_t sock = connect_to(result_host_, result_port_, timeout_ms, error_message);
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
  presence_gate_.close();
  close_socket();
}

}  // namespace seat_aoi
