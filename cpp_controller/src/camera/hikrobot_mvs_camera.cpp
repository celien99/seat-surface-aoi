#include "camera/hikrobot_mvs_camera.hpp"

#include <algorithm>
#include <cstring>
#include <mutex>
#include <sstream>

#include "common/string_utils.hpp"
#include "common/time_utils.hpp"

#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
#include "MvCameraControl.h"
#endif

namespace seat_aoi {

namespace {

#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
constexpr int kMvsOk = MV_OK;
constexpr unsigned int kSupportedDeviceTypes =
    MV_GIGE_DEVICE | MV_USB_DEVICE | MV_GENTL_GIGE_DEVICE |
    MV_GENTL_CAMERALINK_DEVICE | MV_GENTL_CXP_DEVICE | MV_GENTL_XOF_DEVICE;

std::mutex& mvs_sdk_mutex() {
  static std::mutex mutex;
  return mutex;
}

std::uint32_t& mvs_sdk_ref_count() {
  static std::uint32_t ref_count = 0;
  return ref_count;
}

std::string mvs_error(const char* operation, int code) {
  std::ostringstream oss;
  oss << operation << " failed, mvs_code=0x" << std::hex << code;
  return oss.str();
}

bool acquire_mvs_sdk(std::string* error_message) {
  std::lock_guard<std::mutex> lock(mvs_sdk_mutex());
  std::uint32_t& ref_count = mvs_sdk_ref_count();
  if (ref_count == 0) {
    const int ret = MV_CC_Initialize();
    if (ret != kMvsOk) {
      if (error_message != nullptr) {
        *error_message = mvs_error("MV_CC_Initialize", ret);
      }
      return false;
    }
  }
  ++ref_count;
  return true;
}

void release_mvs_sdk() {
  std::lock_guard<std::mutex> lock(mvs_sdk_mutex());
  std::uint32_t& ref_count = mvs_sdk_ref_count();
  if (ref_count == 0) {
    return;
  }
  --ref_count;
  if (ref_count == 0) {
    MV_CC_Finalize();
  }
}

bool set_enum_by_string(void* handle,
                        const char* key,
                        const std::string& value,
                        std::string* error_message) {
  const int ret = MV_CC_SetEnumValueByString(handle, key, value.c_str());
  if (ret == kMvsOk) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = mvs_error(key, ret);
  }
  return false;
}

bool set_float_value(void* handle,
                     const char* key,
                     float value,
                     std::string* error_message) {
  const int ret = MV_CC_SetFloatValue(handle, key, value);
  if (ret == kMvsOk) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = mvs_error(key, ret);
  }
  return false;
}

bool set_int_value(void* handle,
                   const char* key,
                   std::int64_t value,
                   std::string* error_message) {
  const int ret = MV_CC_SetIntValueEx(handle, key, value);
  if (ret == kMvsOk) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = mvs_error(key, ret);
  }
  return false;
}

bool set_bool_value(void* handle,
                    const char* key,
                    bool value,
                    std::string* error_message) {
  const int ret = MV_CC_SetBoolValue(handle, key, value);
  if (ret == kMvsOk) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = mvs_error(key, ret);
  }
  return false;
}

bool set_command_value(void* handle,
                       const char* key,
                       std::string* error_message) {
  const int ret = MV_CC_SetCommandValue(handle, key);
  if (ret == kMvsOk) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = mvs_error(key, ret);
  }
  return false;
}

bool set_image_node_num(void* handle,
                        std::uint32_t value,
                        std::string* error_message) {
  const int ret = MV_CC_SetImageNodeNum(handle, value);
  if (ret == kMvsOk) {
    return true;
  }
  if (error_message != nullptr) {
    *error_message = mvs_error("MV_CC_SetImageNodeNum", ret);
  }
  return false;
}

bool serial_matches(const MV_CC_DEVICE_INFO* info, const std::string& serial_number) {
  if (serial_number.empty() || info == nullptr) {
    return false;
  }
  if (info->nTLayerType == MV_GIGE_DEVICE || info->nTLayerType == MV_GENTL_GIGE_DEVICE) {
    const auto* gige = &info->SpecialInfo.stGigEInfo;
    return serial_number == reinterpret_cast<const char*>(gige->chSerialNumber);
  }
  if (info->nTLayerType == MV_USB_DEVICE) {
    const auto* usb = &info->SpecialInfo.stUsb3VInfo;
    return serial_number == reinterpret_cast<const char*>(usb->chSerialNumber);
  }
  if (info->nTLayerType == MV_GENTL_CAMERALINK_DEVICE) {
    const auto* cml = &info->SpecialInfo.stCMLInfo;
    return serial_number == reinterpret_cast<const char*>(cml->chSerialNumber);
  }
  if (info->nTLayerType == MV_GENTL_CXP_DEVICE) {
    const auto* cxp = &info->SpecialInfo.stCXPInfo;
    return serial_number == reinterpret_cast<const char*>(cxp->chSerialNumber);
  }
  if (info->nTLayerType == MV_GENTL_XOF_DEVICE) {
    const auto* xof = &info->SpecialInfo.stXoFInfo;
    return serial_number == reinterpret_cast<const char*>(xof->chSerialNumber);
  }
  return false;
}

std::uint32_t pixel_format_code(const std::string& pixel_format) {
  if (pixel_format == "Mono8") {
    return PixelType_Gvsp_Mono8;
  }
  return 0;
}

#endif

}  // namespace

HikrobotMvsCamera::~HikrobotMvsCamera() {
  close();
}

bool HikrobotMvsCamera::initialize(const CameraConfig& config) {
  close();
  config_ = config;
  if (config_.pixel_format != "Mono8" || config_.channels != 1) {
    set_error("Hikrobot MVS backend 当前只支持 Mono8 单通道采集");
    return false;
  }
  if (config_.serial_number.empty()) {
    set_error("Hikrobot MVS camera serial_number 不能为空");
    return false;
  }

#ifndef SEAT_AOI_ENABLE_HIKROBOT_MVS
  set_error("Hikrobot MVS SDK 未启用；请使用 -DSEAT_AOI_ENABLE_HIKROBOT_MVS=ON 并配置 MVS include/lib 路径");
  return false;
#else
  std::string error;
  if (!acquire_mvs_sdk(&error)) {
    set_error(error);
    return false;
  }
  sdk_initialized_ = true;

  MV_CC_DEVICE_INFO_LIST devices{};
  int ret = MV_CC_EnumDevices(kSupportedDeviceTypes, &devices);
  if (ret != kMvsOk) {
    set_error(mvs_error("MV_CC_EnumDevices", ret));
    close();
    return false;
  }
  MV_CC_DEVICE_INFO* selected = nullptr;
  for (unsigned int index = 0; index < devices.nDeviceNum; ++index) {
    MV_CC_DEVICE_INFO* candidate = devices.pDeviceInfo[index];
    if (serial_matches(candidate, config_.serial_number)) {
      selected = candidate;
      break;
    }
  }
  if (selected == nullptr) {
    std::ostringstream oss;
    oss << "未找到海康相机 serial_number=" << config_.serial_number
        << " device_count=" << devices.nDeviceNum;
    set_error(oss.str());
    close();
    return false;
  }

  ret = MV_CC_CreateHandle(&handle_, selected);
  if (ret != kMvsOk) {
    set_error(mvs_error("MV_CC_CreateHandle", ret));
    close();
    return false;
  }
  ret = MV_CC_OpenDevice(handle_);
  if (ret != kMvsOk) {
    set_error(mvs_error("MV_CC_OpenDevice", ret));
    close();
    return false;
  }
  if (selected->nTLayerType == MV_GIGE_DEVICE) {
    const int packet_size = MV_CC_GetOptimalPacketSize(handle_);
    if (packet_size > 0) {
      std::string packet_error;
      set_int_value(handle_, "GevSCPSPacketSize", packet_size, &packet_error);
    }
  }

  if (!set_enum_by_string(handle_, "PixelFormat", config_.pixel_format, &error) ||
      !set_int_value(handle_, "Width", config_.width, &error) ||
      !set_int_value(handle_, "Height", config_.height, &error) ||
      !set_enum_by_string(handle_, "AcquisitionMode", "Continuous", &error) ||
      !set_enum_by_string(handle_, "TriggerMode", "On", &error)) {
    set_error(error);
    close();
    return false;
  }

  if (config_.trigger_line.empty()) {
    set_error("Hikrobot MVS trigger_line is required for FL-ACDH strobe capture");
    close();
    return false;
  }
  if (!set_enum_by_string(handle_, "TriggerSource", config_.trigger_line, &error)) {
    set_error(error);
    close();
    return false;
  }

  // 硬件触发模式下设置触发极性
  if (!config_.trigger_line.empty()) {
    if (!set_enum_by_string(handle_, "TriggerActivation", "RisingEdge", &error)) {
      set_error(error);
      close();
      return false;
    }
  }

  if (!set_enum_by_string(handle_, "LineSelector", config_.exposure_output_line, &error) ||
      !set_enum_by_string(handle_, "LineSource", "ExposureStartActive", &error) ||
      !set_bool_value(handle_, "StrobeEnable", true, &error) ||
      !set_int_value(handle_, "StrobeLineDuration", 0, &error) ||
      !set_int_value(handle_, "StrobeLineDelay", 0, &error) ||
      !set_int_value(handle_, "StrobeLinePreDelay", 0, &error) ||
      !set_image_node_num(handle_, std::max<std::uint32_t>(config_.buffer_count, 1U), &error)) {
    set_error(error);
    close();
    return false;
  }
  initialized_ = true;
  healthy_ = true;
  health_message_ = "hikrobot_mvs initialized";
  return true;
#endif
}

void HikrobotMvsCamera::start() {
#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
  if (!initialized_ || handle_ == nullptr) {
    return;
  }
  const int ret = MV_CC_StartGrabbing(handle_);
  if (ret != kMvsOk) {
    set_error(mvs_error("MV_CC_StartGrabbing", ret));
    return;
  }
  grabbing_ = true;
  healthy_ = true;
  health_message_ = "hikrobot_mvs grabbing";
#else
  grabbing_ = false;
#endif
}

void HikrobotMvsCamera::stop() {
#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
  if (handle_ != nullptr && grabbing_) {
    MV_CC_StopGrabbing(handle_);
  }
#endif
  grabbing_ = false;
  armed_ = false;
}

bool HikrobotMvsCamera::arm(std::uint64_t trigger_id,
                            const LightChannelParam& light_param,
                            std::uint32_t light_seq_index,
                            int timeout_ms) {
  if (!initialized_ || !grabbing_ || timeout_ms <= 0 || light_param.light_index == 0 ||
      light_param.exposure_us == 0) {
    set_error("Hikrobot MVS arm 前置状态非法");
    return false;
  }
#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
  std::string error;
  if (config_.trigger_line.empty()) {
    set_error("Hikrobot MVS trigger_line is required for FL-ACDH strobe capture");
    return false;
  }
  if (!set_enum_by_string(handle_, "TriggerSource", config_.trigger_line, &error) ||
      !set_float_value(handle_, "ExposureTime", static_cast<float>(light_param.exposure_us), &error) ||
      !set_float_value(handle_, "Gain", light_param.gain, &error)) {
    set_error(error);
    return false;
  }
#endif
  armed_ = true;
  armed_trigger_id_ = trigger_id;
  armed_light_index_ = light_param.light_index;
  armed_light_seq_index_ = light_seq_index;
  return true;
}

bool HikrobotMvsCamera::wait_frame(std::uint64_t trigger_id,
                                   const LightChannelParam& light_param,
                                   std::uint32_t light_seq_index,
                                   CapturedFrame* out_frame,
                                   int timeout_ms) {
  if (out_frame == nullptr || !initialized_ || !grabbing_ || timeout_ms <= 0 || !armed_ ||
      armed_trigger_id_ != trigger_id ||
      armed_light_index_ != light_param.light_index ||
      armed_light_seq_index_ != light_seq_index) {
    set_error("Hikrobot MVS wait_frame 前置状态非法");
    return false;
  }
#ifndef SEAT_AOI_ENABLE_HIKROBOT_MVS
  set_error("Hikrobot MVS SDK 未启用，无法读取真实图像");
  return false;
#else
  MV_FRAME_OUT frame{};
  const int ret = MV_CC_GetImageBuffer(handle_, &frame, timeout_ms);
  if (ret != kMvsOk) {
    ++dropped_frames_;
    set_error(mvs_error("MV_CC_GetImageBuffer", ret));
    return false;
  }
  const auto release_frame = [&]() {
    MV_CC_FreeImageBuffer(handle_, &frame);
  };
  if (frame.pBufAddr == nullptr || frame.stFrameInfo.nFrameLenEx == 0) {
    release_frame();
    ++dropped_frames_;
    set_error("Hikrobot MVS returned empty frame");
    return false;
  }
  const std::uint32_t expected_pixel_type = pixel_format_code(config_.pixel_format);
  if (expected_pixel_type == 0 || frame.stFrameInfo.enPixelType != expected_pixel_type) {
    release_frame();
    ++dropped_frames_;
    set_error("Hikrobot MVS pixel format mismatch");
    return false;
  }

  const std::uint32_t width = frame.stFrameInfo.nExtendWidth;
  const std::uint32_t height = frame.stFrameInfo.nExtendHeight;
  const std::uint32_t stride = width * config_.channels;
  const std::uint64_t expected_size = static_cast<std::uint64_t>(stride) * height;
  if (width != config_.width || height != config_.height ||
      frame.stFrameInfo.nFrameLenEx < expected_size) {
    release_frame();
    ++dropped_frames_;
    set_error("Hikrobot MVS frame size mismatch");
    return false;
  }

  out_frame->bytes.assign(frame.pBufAddr, frame.pBufAddr + expected_size);
  LightFrameMeta meta{};
  meta.camera_index = config_.camera_index;
  meta.light_index = light_param.light_index;
  meta.frame_index = frame.stFrameInfo.nFrameNum;
  meta.light_seq_index = light_seq_index;
  meta.width = width;
  meta.height = height;
  meta.channels = config_.channels;
  meta.stride_bytes = stride;
  meta.pixel_format = static_cast<std::uint32_t>(PixelFormat::Mono8);
  meta.bit_depth = 8;
  meta.color_order = static_cast<std::uint32_t>(ColorOrder::Mono);
  meta.dtype_code = static_cast<std::uint32_t>(DTypeCode::UInt8);
  meta.timestamp_us = now_us();
  meta.exposure_us = light_param.exposure_us;
  meta.gain = light_param.gain;
  copy_cstr(meta.camera_id, config_.camera_id);
  copy_cstr(meta.view_id, config_.camera_id);
  copy_cstr(meta.calibration_id, config_.calibration_id);
  out_frame->meta = meta;
  release_frame();
  armed_ = false;
  healthy_ = true;
  health_message_ = "hikrobot_mvs frame captured";
  return true;
#endif
}

CameraHealth HikrobotMvsCamera::get_health() const {
  return CameraHealth{healthy_, dropped_frames_, health_message_};
}

void HikrobotMvsCamera::close() {
#ifdef SEAT_AOI_ENABLE_HIKROBOT_MVS
  if (handle_ != nullptr) {
    if (grabbing_) {
      MV_CC_StopGrabbing(handle_);
    }
    MV_CC_CloseDevice(handle_);
    MV_CC_DestroyHandle(handle_);
  }
  if (sdk_initialized_) {
    release_mvs_sdk();
  }
#endif
  handle_ = nullptr;
  sdk_initialized_ = false;
  initialized_ = false;
  grabbing_ = false;
  armed_ = false;
  healthy_ = false;
}

void HikrobotMvsCamera::set_error(const std::string& message) {
  healthy_ = false;
  health_message_ = message;
}

}  // namespace seat_aoi
