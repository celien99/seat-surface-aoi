#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace seat_aoi {

struct CapturedFrame;
struct ImageSaveConfig;

/// 将 Mono8 原始像素数据写入 PNG 格式文件（通过 stb_image_write）。
/// @return true 写入成功
bool write_png(const std::string& path,
               const std::vector<std::uint8_t>& bytes,
               std::uint32_t width,
               std::uint32_t height,
               std::string* error);

/// 将 Mono8 原始像素数据写入 PGM (Portable GrayMap P5) 格式文件。
/// 保留用于调试兼容，主链路默认使用 write_png。
bool write_pgm(const std::string& path,
               const std::vector<std::uint8_t>& bytes,
               std::uint32_t width,
               std::uint32_t height,
               std::string* error);

/// 递归创建目录（跨平台 mkdir -p）
bool make_dirs(const std::string& path, std::string* error);

/// 当前本地日期，格式 YYYYMMDD。
std::string image_save_date_dir();

/// C++ 原始采集图路径：{root}/{YYYYMMDD}/{seat_id}/{camera}_{timestamp}_L{light}_original.png。
std::string build_original_image_path(const ImageSaveConfig& config,
                                      const std::string& date_dir,
                                      const std::string& seat_id,
                                      const CapturedFrame& frame);

/// 当图片根目录所在磁盘可用容量低于阈值时，按文件时间从旧到新删除历史图片。
bool cleanup_old_image_data_if_needed(const ImageSaveConfig& config,
                                      std::string* message);

/// 检测前统一治理业务存储目录：C++ 原图目录与 Python trace 日期目录。
/// 低水位时只清理 YYYYMMDD 日期目录下的历史文件，不删除非业务目录。
bool cleanup_runtime_storage_if_needed(const ImageSaveConfig& config,
                                       const std::string& trace_root,
                                       std::string* message);

/// 清理后仍低于水位时返回 false，用于阻断当前检测并输出 RECHECK。
bool runtime_storage_has_required_free_ratio(const ImageSaveConfig& config,
                                             const std::string& trace_root,
                                             std::string* message);

}  // namespace seat_aoi
