#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

/// 将 Mono8 原始像素数据写入 PGM (Portable GrayMap P5) 格式文件。
/// 格式: P5\n{width} {height}\n255\n<raw pixels>
/// @return true 写入成功
bool write_pgm(const std::string& path,
               const std::vector<std::uint8_t>& bytes,
               std::uint32_t width,
               std::uint32_t height,
               std::string* error);

/// 递归创建目录（跨平台 mkdir -p）
bool make_dirs(const std::string& path, std::string* error);

}  // namespace seat_aoi
