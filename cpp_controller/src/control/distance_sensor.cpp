#include "control/distance_sensor.hpp"

#include <chrono>
#include <cstring>

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

// Modbus RTU CRC-16 查找表
static const std::uint16_t kCrc16Table[256] = {
    0x0000, 0xC0C1, 0xC181, 0x0140, 0xC301, 0x03C0, 0x0280, 0xC241,
    0xC601, 0x06C0, 0x0780, 0xC741, 0x0500, 0xC5C1, 0xC481, 0x0440,
    0xCC01, 0x0CC0, 0x0D80, 0xCD41, 0x0F00, 0xCFC1, 0xCE81, 0x0E40,
    0x0A00, 0xCAC1, 0xCB81, 0x0B40, 0xC901, 0x09C0, 0x0880, 0xC841,
    0xD801, 0x18C0, 0x1980, 0xD941, 0x1B00, 0xDBC1, 0xDA81, 0x1A40,
    0x1E00, 0xDEC1, 0xDF81, 0x1F40, 0xDD01, 0x1DC0, 0x1C80, 0xDC41,
    0x1400, 0xD4C1, 0xD581, 0x1540, 0xD701, 0x17C0, 0x1680, 0xD641,
    0xD201, 0x12C0, 0x1380, 0xD341, 0x1100, 0xD1C1, 0xD081, 0x1040,
    0xF001, 0x30C0, 0x3180, 0xF141, 0x3300, 0xF3C1, 0xF281, 0x3240,
    0x3600, 0xF6C1, 0xF781, 0x3740, 0xF501, 0x35C0, 0x3480, 0xF441,
    0x3C00, 0xFCC1, 0xFD81, 0x3D40, 0xFF01, 0x3FC0, 0x3E80, 0xFE41,
    0xFA01, 0x3AC0, 0x3B80, 0xFB41, 0x3900, 0xF9C1, 0xF881, 0x3840,
    0x2800, 0xE8C1, 0xE981, 0x2940, 0xEB01, 0x2BC0, 0x2A80, 0xEA41,
    0xEE01, 0x2EC0, 0x2F80, 0xEF41, 0x2D00, 0xEDC1, 0xEC81, 0x2C40,
    0xE401, 0x24C0, 0x2580, 0xE541, 0x2700, 0xE7C1, 0xE681, 0x2640,
    0x2200, 0xE2C1, 0xE381, 0x2340, 0xE101, 0x21C0, 0x2080, 0xE041,
    0xA001, 0x60C0, 0x6180, 0xA141, 0x6300, 0xA3C1, 0xA281, 0x6240,
    0x6600, 0xA6C1, 0xA781, 0x6740, 0xA501, 0x65C0, 0x6480, 0xA441,
    0x6C00, 0xACC1, 0xAD81, 0x6D40, 0xAF01, 0x6FC0, 0x6E80, 0xAE41,
    0xAA01, 0x6AC0, 0x6B80, 0xAB41, 0x6900, 0xA9C1, 0xA881, 0x6840,
    0x7800, 0xB8C1, 0xB981, 0x7940, 0xBB01, 0x7BC0, 0x7A80, 0xBA41,
    0xBE01, 0x7EC0, 0x7F80, 0xBF41, 0x7D00, 0xBDC1, 0xBC81, 0x7C40,
    0xB401, 0x74C0, 0x7580, 0xB541, 0x7700, 0xB7C1, 0xB681, 0x7640,
    0x7200, 0xB2C1, 0xB381, 0x7340, 0xB101, 0x71C0, 0x7080, 0xB041,
    0x5000, 0x90C1, 0x9181, 0x5140, 0x9301, 0x53C0, 0x5280, 0x9241,
    0x9601, 0x56C0, 0x5780, 0x9741, 0x5500, 0x95C1, 0x9481, 0x5440,
    0x9C01, 0x5CC0, 0x5D80, 0x9D41, 0x5F00, 0x9FC1, 0x9E81, 0x5E40,
    0x5A00, 0x9AC1, 0x9B81, 0x5B40, 0x9901, 0x59C0, 0x5880, 0x9841,
    0x8801, 0x48C0, 0x4980, 0x8941, 0x4B00, 0x8BC1, 0x8A81, 0x4A40,
    0x4E00, 0x8EC1, 0x8F81, 0x4F40, 0x8D01, 0x4DC0, 0x4C80, 0x8C41,
    0x4400, 0x84C1, 0x8581, 0x4540, 0x8701, 0x47C0, 0x4680, 0x8641,
    0x8201, 0x42C0, 0x4380, 0x8341, 0x4100, 0x81C1, 0x8081, 0x4040,
};

std::uint64_t now_ms() {
  return static_cast<std::uint64_t>(
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now().time_since_epoch())
          .count());
}

}  // namespace

std::uint16_t DistanceSensor::crc16(const std::uint8_t* data, std::size_t length) {
  std::uint16_t crc = 0xFFFF;
  for (std::size_t i = 0; i < length; ++i) {
    crc = (crc >> 8) ^ kCrc16Table[(crc ^ data[i]) & 0xFF];
  }
  return crc;
}

DistanceSensor::~DistanceSensor() {
  shutdown();
}

#ifdef _WIN32
// Minimal Win32 RS485 serial — same pattern as FlAcdhLightController
void DistanceSensor::close_serial() {
  if (handle_ != nullptr) { CloseHandle(handle_); handle_ = nullptr; }
}

bool DistanceSensor::initialize(const DistanceSensorConfig& config, std::string* error_message) {
  close_serial();
  config_ = config;
  std::string port = "\\\\.\\" + config.serial_port;
  handle_ = CreateFileA(port.c_str(), GENERIC_READ | GENERIC_WRITE, 0, nullptr,
                        OPEN_EXISTING, 0, nullptr);
  if (handle_ == INVALID_HANDLE_VALUE) {
    if (error_message) *error_message = "DistanceSensor: cannot open " + config.serial_port;
    return false;
  }
  DCB dcb{sizeof(DCB)};
  GetCommState(handle_, &dcb);
  dcb.BaudRate = config.baud_rate; dcb.ByteSize = 8; dcb.Parity = NOPARITY; dcb.StopBits = ONESTOPBIT;
  SetCommState(handle_, &dcb);
  COMMTIMEOUTS to{}; to.ReadTotalTimeoutConstant = config.poll_interval_ms; SetCommTimeouts(handle_, &to);
  initialized_ = true;
  return true;
}

void DistanceSensor::shutdown() { close_serial(); initialized_ = false; }

int DistanceSensor::read_distance_mm(std::string* error_message) {
  if (!initialized_) return -1;
  std::uint8_t req[8] = {
      static_cast<std::uint8_t>(config_.slave_address), 0x03, 0x00, 0x00, 0x00, 0x02, 0, 0};
  std::uint16_t c = crc16(req, 6); req[6] = c & 0xFF; req[7] = c >> 8;
  DWORD w = 0; WriteFile(handle_, req, 8, &w, nullptr);
  std::uint8_t buf[9]; DWORD r = 0; ReadFile(handle_, buf, 9, &r, nullptr);
  if (r < 7) return -1;
  return (static_cast<int>(buf[3]) << 24) | (static_cast<int>(buf[4]) << 16)
       | (static_cast<int>(buf[5]) << 8)  | static_cast<int>(buf[6]);
}

#else  // POSIX

void DistanceSensor::close_serial() {
  if (fd_ >= 0) { ::close(fd_); fd_ = -1; }
}

bool DistanceSensor::initialize(const DistanceSensorConfig& config, std::string* error_message) {
  close_serial();
  config_ = config;
  int fd = ::open(config.serial_port.c_str(), O_RDWR | O_NOCTTY);
  if (fd < 0) {
    if (error_message) *error_message = "DistanceSensor: cannot open " + config.serial_port;
    return false;
  }
  struct termios tty{}; tcgetattr(fd, &tty);
  speed_t s = B9600;
  switch (config.baud_rate) {
    case 4800: s=B4800; break; case 19200: s=B19200; break; case 38400: s=B38400; break;
    case 57600: s=B57600; break; case 115200: s=B115200; break; default: s=B9600; break;
  }
  cfsetospeed(&tty, s); cfsetispeed(&tty, s);
  tty.c_cflag &= ~PARENB; tty.c_cflag &= ~CSTOPB; tty.c_cflag &= ~CSIZE; tty.c_cflag |= CS8;
  tty.c_cflag |= CLOCAL | CREAD;
  tty.c_iflag &= ~(IXON | IXOFF | IXANY); tty.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
  tty.c_cc[VMIN] = 0; tty.c_cc[VTIME] = 1;
  tcsetattr(fd, TCSANOW, &tty);
  fd_ = fd;
  initialized_ = true;
  return true;
}

void DistanceSensor::shutdown() { close_serial(); initialized_ = false; }

int DistanceSensor::read_distance_mm(std::string* error_message) {
  if (!initialized_) return -1;
  // Modbus RTU: 功能码 0x03, 寄存器 0x0000, 2 个寄存器 (uint32)
  std::uint8_t req[8] = {
      static_cast<std::uint8_t>(config_.slave_address), 0x03, 0x00, 0x00, 0x00, 0x02, 0, 0};
  std::uint16_t c = crc16(req, 6); req[6] = c & 0xFF; req[7] = c >> 8;
  tcflush(fd_, TCIOFLUSH);
  if (::write(fd_, req, 8) < 8) return -1;
  std::uint8_t buf[9]{};
  struct pollfd pfd{fd_, POLLIN, 0};
  if (::poll(&pfd, 1, static_cast<int>(config_.poll_interval_ms)) <= 0) return -1;
  const ssize_t n = ::read(fd_, buf, 9);
  if (n < 7) return -1;
  // 大端 uint32: buf[3..6]
  return (static_cast<int>(buf[3]) << 24) | (static_cast<int>(buf[4]) << 16)
       | (static_cast<int>(buf[5]) << 8)  | static_cast<int>(buf[6]);
}

#endif

bool DistanceSensor::poll_trigger() {
  if (!initialized_) return false;

  const int dist = read_distance_mm(nullptr);
  const std::uint64_t now = now_ms();

  switch (state_) {
    case State::Armed:
      if (dist >= 0 && static_cast<std::uint32_t>(dist) < config_.threshold_mm) {
        state_ = State::Debouncing;
        debounce_start_ms_ = now;
      }
      break;
    case State::Debouncing:
      if (dist < 0 || static_cast<std::uint32_t>(dist) >= config_.threshold_mm) {
        state_ = State::Armed;
        break;
      }
      if (now - debounce_start_ms_ >= config_.trigger_delay_ms) {
        state_ = State::Triggered;
        cooldown_start_ms_ = now;
        return true;
      }
      break;
    case State::Triggered:
      // 冷却：距离 >= 阈值且 >= 2s
      if (dist >= 0 && static_cast<std::uint32_t>(dist) >= config_.threshold_mm
          && now - cooldown_start_ms_ >= 2000) {
        state_ = State::Armed;
      }
      break;
  }
  return false;
}

void DistanceSensor::reset_trigger() {
  state_ = State::Armed;
}

}  // namespace seat_aoi
