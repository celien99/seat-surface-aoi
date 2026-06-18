#include "control/fl_acdh_light_controller.hpp"

#include <cstdio>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>
#include <string>

#ifdef _WIN32
#include <windows.h>
#else
#include <fcntl.h>
#include <poll.h>
#include <termios.h>
#include <unistd.h>
#endif

namespace seat_aoi {

namespace {

constexpr int kDefaultBaudRate = 9600;
constexpr int kSerialTimeoutMs = 200;

bool valid_channel_param(const LightChannelParam& channel) {
  return channel.light_index != 0 && channel.physical_channel != 0 &&
         channel.exposure_us != 0 && channel.strobe_width_us != 0 &&
         channel.current_percent > 0.0F && channel.current_percent <= 100.0F;
}

}  // namespace

// ============================================================================
// 析构
// ============================================================================

FlAcdhLightController::~FlAcdhLightController() {
  close();
}

// ============================================================================
// 协议工具函数
// ============================================================================

std::string FlAcdhLightController::compute_checksum(const std::string& payload) {
  unsigned char checksum = 0;
  for (const auto ch : payload) {
    checksum ^= static_cast<unsigned char>(ch);
  }
  std::ostringstream oss;
  oss << std::uppercase << std::hex << std::setw(2) << std::setfill('0')
      << static_cast<int>(checksum);
  return oss.str();
}

std::string FlAcdhLightController::build_frame(char cmd, char channel,
                                                const std::string& value) {
  // payload = $ + cmd + channel + value (before checksum)
  std::string payload;
  payload.reserve(1 + 1 + 1 + 3);
  payload.push_back('$');
  payload.push_back(cmd);
  payload.push_back(channel);
  payload.append(value);
  const std::string checksum = compute_checksum(payload);
  std::string frame;
  frame.reserve(payload.size() + 2 + 2);
  frame = payload;
  frame.append(checksum);
  frame.append("\r\n");
  return frame;
}

char FlAcdhLightController::channel_char(std::uint32_t physical_channel) {
  // 1-4 -> '1'-'4'
  return static_cast<char>('0' + physical_channel);
}

std::string FlAcdhLightController::format_strobe_width(std::uint32_t strobe_width_us) {
  // 3 位十进制，零填充，如 100 -> "100", 50 -> "050"
  std::ostringstream oss;
  oss << std::setw(3) << std::setfill('0') << strobe_width_us;
  return oss.str();
}

std::string FlAcdhLightController::format_delay(std::uint32_t trigger_delay_us) {
  std::ostringstream oss;
  oss << std::setw(3) << std::setfill('0') << trigger_delay_us;
  return oss.str();
}

// ============================================================================
// 串口操作（平台相关）
// ============================================================================

#ifdef _WIN32

bool FlAcdhLightController::open_serial(const std::string& port,
                                         std::uint32_t baud_rate,
                                         std::string* error_message) {
  close_serial();

  std::string device_path = "\\\\.\\" + port;
  HANDLE h = CreateFileA(device_path.c_str(),
                         GENERIC_READ | GENERIC_WRITE,
                         0,
                         nullptr,
                         OPEN_EXISTING,
                         0,
                         nullptr);
  if (h == INVALID_HANDLE_VALUE) {
    if (error_message != nullptr) {
      std::ostringstream oss;
      oss << "FL-ACDH 无法打开串口 " << port << " (err=" << GetLastError() << ")";
      *error_message = oss.str();
    }
    return false;
  }

  DCB dcb{};
  dcb.DCBlength = sizeof(DCB);
  if (!GetCommState(h, &dcb)) {
    CloseHandle(h);
    if (error_message != nullptr) {
      *error_message = "FL-ACDH GetCommState 失败";
    }
    return false;
  }
  dcb.BaudRate = baud_rate;
  dcb.ByteSize = 8;
  dcb.Parity = NOPARITY;
  dcb.StopBits = ONESTOPBIT;
  dcb.fBinary = TRUE;
  dcb.fDtrControl = DTR_CONTROL_DISABLE;
  dcb.fRtsControl = RTS_CONTROL_DISABLE;
  if (!SetCommState(h, &dcb)) {
    CloseHandle(h);
    if (error_message != nullptr) {
      *error_message = "FL-ACDH SetCommState 失败";
    }
    return false;
  }

  COMMTIMEOUTS timeouts{};
  timeouts.ReadIntervalTimeout = MAXDWORD;
  timeouts.ReadTotalTimeoutMultiplier = MAXDWORD;
  timeouts.ReadTotalTimeoutConstant = kSerialTimeoutMs;
  timeouts.WriteTotalTimeoutMultiplier = 0;
  timeouts.WriteTotalTimeoutConstant = kSerialTimeoutMs;
  SetCommTimeouts(h, &timeouts);

  handle_ = h;
  return true;
}

void FlAcdhLightController::close_serial() {
  if (handle_ != nullptr) {
    CloseHandle(handle_);
    handle_ = nullptr;
  }
}

int FlAcdhLightController::write_serial(const void* data, std::size_t size,
                                         int timeout_ms) {
  if (handle_ == nullptr) {
    return -1;
  }
  COMMTIMEOUTS timeouts{};
  timeouts.ReadIntervalTimeout = MAXDWORD;
  timeouts.ReadTotalTimeoutMultiplier = MAXDWORD;
  timeouts.ReadTotalTimeoutConstant = kSerialTimeoutMs;
  timeouts.WriteTotalTimeoutMultiplier = 0;
  timeouts.WriteTotalTimeoutConstant =
      timeout_ms < 0 ? 0 : static_cast<DWORD>(timeout_ms);
  SetCommTimeouts(handle_, &timeouts);

  DWORD written = 0;
  if (!WriteFile(handle_, data, static_cast<DWORD>(size), &written, nullptr)) {
    return -1;
  }
  return static_cast<int>(written);
}

int FlAcdhLightController::read_serial(void* buffer, std::size_t size,
                                        int timeout_ms) {
  if (handle_ == nullptr) {
    return -1;
  }
  COMMTIMEOUTS timeouts{};
  timeouts.ReadIntervalTimeout = MAXDWORD;
  timeouts.ReadTotalTimeoutMultiplier = MAXDWORD;
  timeouts.ReadTotalTimeoutConstant =
      timeout_ms < 0 ? 0 : static_cast<DWORD>(timeout_ms);
  timeouts.WriteTotalTimeoutMultiplier = 0;
  timeouts.WriteTotalTimeoutConstant = kSerialTimeoutMs;
  SetCommTimeouts(handle_, &timeouts);

  DWORD read_bytes = 0;
  if (!ReadFile(handle_, buffer, static_cast<DWORD>(size), &read_bytes, nullptr)) {
    return -1;
  }
  return static_cast<int>(read_bytes);
}

#else  // POSIX

bool FlAcdhLightController::open_serial(const std::string& port,
                                         std::uint32_t baud_rate,
                                         std::string* error_message) {
  close_serial();

  int fd = ::open(port.c_str(), O_RDWR | O_NOCTTY);
  if (fd < 0) {
    if (error_message != nullptr) {
      std::ostringstream oss;
      oss << "FL-ACDH 无法打开串口 " << port << " (errno=" << errno << ")";
      *error_message = oss.str();
    }
    return false;
  }

  struct termios tty{};
  if (tcgetattr(fd, &tty) != 0) {
    ::close(fd);
    if (error_message != nullptr) {
      *error_message = "FL-ACDH tcgetattr 失败";
    }
    return false;
  }

  // 设置波特率
  speed_t speed = B9600;
  switch (baud_rate) {
    case 4800:  speed = B4800;  break;
    case 19200: speed = B19200; break;
    case 38400: speed = B38400; break;
    case 57600: speed = B57600; break;
    case 115200: speed = B115200; break;
    default:    speed = B9600;  break;
  }
  cfsetospeed(&tty, speed);
  cfsetispeed(&tty, speed);

  // 8N1
  tty.c_cflag &= ~PARENB;
  tty.c_cflag &= ~CSTOPB;
  tty.c_cflag &= ~CSIZE;
  tty.c_cflag |= CS8;
  tty.c_cflag |= CLOCAL | CREAD;

  // 关闭软件流控
  tty.c_iflag &= ~(IXON | IXOFF | IXANY);
  // 关闭硬件流控
  tty.c_cflag &= ~CRTSCTS;

  // 原始模式
  tty.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
  tty.c_iflag &= ~(INLCR | ICRNL | IGNCR);
  tty.c_oflag &= ~OPOST;

  // 读取超时：0 字节间超时，total_timeout 用于总超时
  tty.c_cc[VMIN] = 0;
  tty.c_cc[VTIME] = 0;

  if (tcsetattr(fd, TCSANOW, &tty) != 0) {
    ::close(fd);
    if (error_message != nullptr) {
      *error_message = "FL-ACDH tcsetattr 失败";
    }
    return false;
  }

  fd_ = fd;
  return true;
}

void FlAcdhLightController::close_serial() {
  if (fd_ >= 0) {
    ::close(fd_);
    fd_ = -1;
  }
}

int FlAcdhLightController::write_serial(const void* data, std::size_t size,
                                         int /*timeout_ms*/) {
  if (fd_ < 0) {
    return -1;
  }
  const ssize_t written = ::write(fd_, data, size);
  if (written < 0) {
    return -1;
  }
  return static_cast<int>(written);
}

int FlAcdhLightController::read_serial(void* buffer, std::size_t size,
                                        int timeout_ms) {
  if (fd_ < 0) {
    return -1;
  }
  struct pollfd pfd{};
  pfd.fd = fd_;
  pfd.events = POLLIN;
  const int poll_ret = ::poll(&pfd, 1, timeout_ms < 0 ? kSerialTimeoutMs : timeout_ms);
  if (poll_ret <= 0) {
    return 0;  // 超时或无数据
  }
  const ssize_t n = ::read(fd_, buffer, size);
  if (n < 0) {
    return -1;
  }
  return static_cast<int>(n);
}

#endif

// ============================================================================
// 帧收发
// ============================================================================

bool FlAcdhLightController::send_frame(const std::string& frame,
                                        int timeout_ms,
                                        std::string* error_message) {
#ifdef _WIN32
  const bool is_open = (handle_ != nullptr);
#else
  const bool is_open = (fd_ >= 0);
#endif
  if (!is_open) {
    if (error_message != nullptr) {
      *error_message = "FL-ACDH 串口未打开";
    }
    return false;
  }

  // 打印发送帧（调试用）
  std::cout << "FL-ACDH " << serial_port_ << " -> " << frame;

  const int written = write_serial(frame.data(), frame.size(), timeout_ms);
  if (written != static_cast<int>(frame.size())) {
    if (error_message != nullptr) {
      std::ostringstream oss;
      oss << "FL-ACDH 串口写入失败 (expected=" << frame.size()
          << " actual=" << written << ")";
      *error_message = oss.str();
    }
    return false;
  }

  // 读取 1 字节响应
  unsigned char response = 0;
  const int n = read_serial(&response, 1, timeout_ms);
  if (n <= 0) {
    if (error_message != nullptr) {
      *error_message = "FL-ACDH 串口响应超时";
    }
    return false;
  }

  std::cout << "FL-ACDH " << serial_port_ << " <- "
            << static_cast<char>(response) << std::endl;

  if (response == '$') {
    return true;
  }
  if (response == '&') {
    if (error_message != nullptr) {
      *error_message = "FL-ACDH 控制器拒绝命令";
    }
    return false;
  }
  if (error_message != nullptr) {
    std::ostringstream oss;
    oss << "FL-ACDH 未知响应 0x" << std::hex << static_cast<int>(response);
    *error_message = oss.str();
  }
  return false;
}

bool FlAcdhLightController::send_command(char cmd, char channel,
                                          const std::string& value,
                                          bool allow_rejection,
                                          int timeout_ms,
                                          std::string* error_message) {
  const std::string frame = build_frame(cmd, channel, value);
  std::string cmd_error;
  const bool ok = send_frame(frame, timeout_ms, &cmd_error);
  if (ok) {
    return true;
  }
  if (allow_rejection) {
    // C/B 命令：记录日志但继续执行
    std::cout << "FL-ACDH " << serial_port_ << " cmd " << cmd
              << " rejected (non-critical): " << cmd_error << std::endl;
    return true;  // 不被视为失败
  }
  if (error_message != nullptr) {
    std::ostringstream oss;
    oss << "FL-ACDH cmd " << cmd << " ch=" << channel << " failed: " << cmd_error;
    *error_message = oss.str();
  }
  return false;
}

// ============================================================================

// ============================================================================
// ILightController 接口实现（对齐 Deploy：每步 = 配置+点火完整序列）
// ============================================================================

bool FlAcdhLightController::initialize(const LightControllerConfig& config) {
  close();

  serial_port_ = config.serial_port;
  baud_rate_ = config.baud_rate > 0 ? config.baud_rate : kDefaultBaudRate;
  simulate_fault_ = config.simulate_fault;

  if (serial_port_.empty()) {
    std::cerr << "FL-ACDH serial_port 未配置" << std::endl;
    return false;
  }

  std::string open_error;
  if (!open_serial(serial_port_, baud_rate_, &open_error)) {
    std::cerr << open_error << std::endl;
    return false;
  }

  std::cout << "FL-ACDH 串口已打开 " << serial_port_ << " @ " << baud_rate_
            << " baud" << std::endl;

  initialized_ = true;
  trigger_count_ = 0;
  last_light_index_ = 0;
  last_physical_channel_ = 0;
  return true;
}

bool FlAcdhLightController::prepare_sequence(const LightSequence& sequence,
                                              std::uint64_t /*trigger_id*/,
                                              int timeout_ms,
                                              std::string* error_message) {
  if (!initialized_ || sequence.channels.empty() || timeout_ms <= 0) {
    if (error_message != nullptr)
      *error_message = "FL-ACDH 未初始化、序列为空或 timeout_ms 非法";
    return false;
  }
  if (simulate_fault_) {
    if (error_message != nullptr) *error_message = "FL-ACDH 模拟光源故障";
    shutdown_all();
    return false;
  }
  for (const auto& channel : sequence.channels) {
    if (!channel.enabled) continue;
    if (channel.physical_channel == 0 || channel.strobe_width_us == 0 ||
        channel.current_percent <= 0.0F || channel.current_percent > 100.0F) {
      if (error_message != nullptr) {
        *error_message = "FL-ACDH 光源通道 light_index=" +
                         std::to_string(channel.light_index) + " 参数非法";
      }
      shutdown_all();
      return false;
    }
  }
  return true;
}

bool FlAcdhLightController::trigger_channel(const LightChannelParam& channel,
                                             std::uint64_t /*trigger_id*/,
                                             std::uint32_t light_seq_index,
                                             int timeout_ms,
                                             std::string* error_message) {
  if (!initialized_ || timeout_ms <= 0 ||
      channel.physical_channel == 0 || channel.strobe_width_us == 0) {
    if (error_message != nullptr)
      *error_message = "FL-ACDH trigger_channel 参数非法";
    shutdown_all();
    return false;
  }
  if (!channel.enabled) return true;
  if (simulate_fault_) {
    if (error_message != nullptr) *error_message = "FL-ACDH 模拟光源故障";
    shutdown_all();
    return false;
  }

  const char ch = channel_char(channel.physical_channel);
  const std::string strobe_val = format_strobe_width(channel.strobe_width_us);
  const std::string delay_val = format_delay(channel.trigger_delay_us);

  std::cout << "FL-ACDH " << serial_port_ << " ch=" << channel.physical_channel
            << " strobe=" << channel.strobe_width_us
            << "us delay=" << channel.trigger_delay_us
            << "us light_seq_index=" << light_seq_index << std::endl;

  // 对齐 Deploy：每条命令按 C->B->8->9->A->7 顺序发送
  if (!send_command('C', ch, "000", true, timeout_ms, error_message)) return false;
  if (!send_command('B', ch, "001", true, timeout_ms, error_message)) return false;
  if (!send_command('8', ch, "000", false, timeout_ms, error_message)) return false;
  if (!send_command('9', ch, strobe_val, false, timeout_ms, error_message)) return false;
  if (!send_command('A', ch, delay_val, false, timeout_ms, error_message)) return false;
  if (!send_command('7', ch, "000", false, timeout_ms, error_message)) return false;

  std::cout << "FL-ACDH " << serial_port_ << " ch=" << channel.physical_channel
            << " triggered" << std::endl;

  ++trigger_count_;
  last_light_index_ = channel.light_index;
  last_physical_channel_ = channel.physical_channel;
  return true;
}

LightHealth FlAcdhLightController::get_health() const {
  LightHealth health;
#ifdef _WIN32
  const bool serial_open = (handle_ != nullptr);
#else
  const bool serial_open = (fd_ >= 0);
#endif
  health.ok = initialized_ && serial_open && !simulate_fault_;
  health.ready = initialized_ && serial_open && !simulate_fault_;
  health.trigger_count = trigger_count_;
  health.last_light_index = last_light_index_;
  health.last_physical_channel = last_physical_channel_;
  if (!serial_open)
    health.message = "FL-ACDH 串口未打开";
  else if (simulate_fault_)
    health.message = "FL-ACDH 模拟故障";
  else
    health.message = "FL-ACDH serial " + serial_port_ + " @ " + std::to_string(baud_rate_);
  return health;
}

void FlAcdhLightController::shutdown_all() {
  initialized_ = false;
  close_serial();
}

void FlAcdhLightController::close() {
  shutdown_all();
}

}  // namespace seat_aoi
