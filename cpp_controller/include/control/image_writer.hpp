#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

struct CapturedFrame;
struct ImageSaveConfig;

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

/// 当前本地日期，格式 YYYYMMDD。
std::string image_save_date_dir();

/// C++ 原始采集图路径：{root}/{YYYYMMDD}/{seat_id}/{camera}_{timestamp}_L{light}_original.pgm。
std::string build_original_image_path(const ImageSaveConfig& config,
                                      const std::string& date_dir,
                                      const std::string& seat_id,
                                      const CapturedFrame& frame);

/// 当图片根目录所在磁盘可用容量低于阈值时，按文件时间从旧到新删除历史图片。
bool cleanup_old_image_data_if_needed(const ImageSaveConfig& config,
                                      std::string* message);

}  // namespace seat_aoi
