# 工控机上线补齐报告

> 目标工位：固定双机位 + 三路共享频闪
> 生成日期：2026-06-18

---

## 一、硬件清单

| 设备 | 型号/规格 | 数量 | 标识/序列号 |
|------|-----------|------|-------------|
| 工业相机 | 海康 MV-CH120-20GC，4096×3072，Mono8 | 2 | DA9184656 / DA9184665 |
| FA 镜头 | MVL-KF0814M-12MPE，8mm F1.4，1.1"，C 接口 | 2 | — |
| 频闪控制器 | FL-ACDH-20048-4（4 通道，使用通道 1/2/3） | 1 | — |
| 光源 | 3 组 | 3 | — |
| 工控机 | Windows 10/11 x64 | 1 | — |
| 网线 | 触发信号 TCP 直连 + 相机 GigE | 3+ | — |
| RS232 转 USB | FL-ACDH 串口通信 | 1 | — |

---

## 二、硬件接线图

```
                        ┌───────────────────────────────┐
                        │        工控机 (Windows)         │
                        │                               │
     触发端 ──网线──→   │  TCP :9000 (tcp_signal)       │
                        │                               │
                        │  USB-RS232 ──→ FL-ACDH 串口   │
                        │       ↑                       │
                        │    COM1 (设备管理器确认)        │
                        │                               │
   DA9184656 ──网线──→  │  GigE 相机 0 (TOP_BACK)       │
   DA9184665 ──网线──→  │  GigE 相机 1 (TOP_CUSHION)    │
                        └───────────────────────────────┘

            FL-ACDH-20048-4 面板接线
            ═══════════════════════════
            串口 (RS232)  ←── 工控机 USB-RS232
            F1~F3 短接合成触发总线
            触发总线 (同步输出) ─┬── 相机A 黄色 Line0 (并联)
                                 └── 相机B 黄色 Line0 (并联)
            通道 1  ──→ 光源 1 (主照明)
            通道 2  ──→ 光源 2 (侧向补光)
            通道 3  ──→ 光源 3 (低角度打光)
            通道 4    （预留）
```

### 接线验证要点

- [ ] 设备管理器 → "端口 (COM 和 LPT)" → 确认 USB-RS232 的 COM 号
- [ ] 设备管理器 → "网络适配器" → 确认两个 GigE 相机网卡已配置（建议 MTU 9000 Jumbo Frame）
- [ ] MVS 客户端 → 枚举设备 → 确认 `DA9184656` / `DA9184665` 在线
- [ ] FL-ACDH 上电 → RS232 串口通信测试（说明书默认 9600 8N1）
- [ ] 相机黄色 Line0 收到 FL-ACDH F1~F3 合线后的同步信号（示波器确认波形的上升沿触发）

---

## 三、配置自检矩阵

### ✅ 已固化（不需修改，当前配置已体现）

| 项目 | 文件 | 状态 |
|------|------|------|
| 相机序列号 | `station_runtime.production.conf` | ✅ `DA9184656` / `DA9184665` |
| 相机分辨率 | C++ config + Python calibration | ✅ 4096×3072 |
| 频闪后端 | `light.backend=serial_ascii` | ✅ FL-ACDH RS232 |
| 相机后端 | `camera.backend=hikrobot_mvs` | ✅ MVS SDK |
| 信号后端 | `signal.backend=tcp_signal` | ✅ TCP 监听模式 |
| 共享内存 | `slot_count=4, frame_slot_size=128 MB` | ✅ 容量 OK |
| 运行模式 | `hardware_mode=production` | ✅ |
| 故障注入 | 全部 `false` | ✅ |

### 🔴 阻塞项（工控机实测前必须确认）

| # | 参数 | 文件 | 当前值 | 操作 |
|---|------|------|--------|------|
| 1 | COM 端口 | `station_runtime.production.conf` | `COM1` | 设备管理器确认实际 COM 号 |
| 2 | 结果回传端 IP | `station_runtime.production.conf` | `192.168.10.100` | 填写触发/上位机结果接收端真实 IP；若复用触发 TCP 客户端回传，可留空 `result_host/result_port` |

### 🟡 功能项（可后补，不阻运行）

| # | 项目 | 文件 | 说明 |
|---|------|------|------|
| 4 | 标定对齐矩阵 | `python_detector/config/calibration/` | 当前全单位阵，多光源配准无偏移 |
| 5 | JSON 输出 | `json_output.enabled=false` | 当前关闭，无影响 |
| 6 | 图像落盘 | `image_save.enabled=false` | 生产环境建议关闭；启用时写入 `images/YYYYMMDD/<seat_id>/`，可用容量低于 20% 时按文件时间清理最早图片 |
| 7 | 站位 ID | `signal.station_id=LINE1_AOI_01` | 可自定义 |
| 8 | 第 4 路检测光源 | FL-ACDH 通道 4 | 当前预留，不属于产线必需检测光源；当前 C++ 生产配置只采集 `light_order=1,2,3` |

---

## 四、三检测光源生产配方已对齐

当前产线明确为固定双机位 + 三路共享频闪。C++ 当前采集 `light_order=1,2,3`，Python 生产配方的三路必需检测光源与 C++ 采集顺序保持一致。

| 层 | 文件 | 光源配置 |
|----|------|----------|
| C++ config | `station_runtime.production.conf` | `light_order=1,2,3` |
| C++→Python 映射 | `python_detector/ipc/shm_client.py` | `1→DIFFUSE, 2→POLAR_DIFFUSE, 3→HIGH_LEFT` |
| Python recipe | `production_recipe.yaml` | `required_lights: [DIFFUSE, POLAR_DIFFUSE, HIGH_LEFT]` |
| Python model input | `production_recipe.yaml` | `ch0_diffuse/ch1_polar_diffuse/ch2_high_left` |

质量门禁会要求这 3 路检测光源全部存在、时间戳按配置顺序单调、帧号/光源序号唯一、曝光/增益一致、亮度和配准通过。当前 Python 生产配方将 `DOME` 语义暂映射到 `DIFFUSE`，ROI 定位复用第一路频闪图，不额外要求 C++ 发布 `DOME_ROI` 采集轮次。缺任一路检测光源、超时、CRC/协议错误或质量门禁失败仍会返回 `RECHECK` 或 `ERROR`，不会输出 `OK`。

如果未来新增常亮 Dome ROI 或第 4 路 `HIGH_RIGHT`，需要同时修改 C++ `light_order` 和对应 `light.<N>.*`、C++ 采集编排、Python `production_recipe.yaml` 的语义光源/`required_lights`/`input_channels`、模型训练资产和相关测试。

---

## 五、模型资产（当前已补齐，放行以校验命令为准）

| 文件 | 用途 | 放行要求 |
|------|------|----------|
| `model/roi_yolo/seat_roi_seg.onnx` | Dome/DIFFUSE 语义光源 ROI segmentation 定位 | 文件存在、非占位，类别与 `roi_locator.class_names: [seat]` 一致 |
| `model/wideresnet50/seat_wrn50_embedding.onnx` | 多光源 ROI 空间 embedding 特征提取 | 输入通道数与生产配方 `input_channels` 一致，空间层输出维度与配方一致 |
| `model/patchcore/seat_pca.json` | embedding 降维参数 | `pca_version=pca_seat_v2`，输入/输出维度与 memory bank 一致 |
| `model/patchcore/seat_patchcore_bank.json` | PatchCore memory bank | PCA 维度、向量数、版本元数据通过校验 |
| `model/patchcore/seat_patchcore.faiss` | FAISS 加速索引 | 可选；启用时维度和向量数必须与 memory bank 一致 |

正式上线前必须运行：

```powershell
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1
uv run python -m tools.validate_deployment_preflight --strict-production
```

若任一模型缺失、仍是占位文件、维度不一致或后端依赖缺失，Python pipeline 会返回 `RECHECK`（`error_code=CONFIGURATION_ERROR`），trace 保存 `raw_images/` 原始采集图，display_app 正常展示原图；不会输出 `OK`。

---

## 六、构建与环境要求

### 工控机软件依赖

| 依赖 | 说明 | 安装方式 |
|------|------|----------|
| Visual Studio 2019+ Build Tools | C++17 MSVC 编译器 | VS Installer |
| CMake ≥ 3.16 | 构建系统 | cmake.org |
| 海康 MVS SDK | 相机驱动 + 开发库 | 海康官网下载 |
| Python 3.10+ | 检测进程 | python.org；无网现场需提前准备离线安装包 |
| uv | 有网开发机依赖锁定和打包工具 | 工控机无网时可不现场联网安装，随离线工具包或安装介质交付 |
| Python 离线依赖包 | detector/display/ONNX/FAISS wheelhouse | 有网同平台机器执行 `uv run python -m tools.package_python_offline_deps --extra display --extra onnx --extra faiss` |

### C++ 构建（启用海康 MVS）

```powershell
cd cpp_controller
cmake -B build -DCMAKE_BUILD_TYPE=Release `
  -DSEAT_AOI_ENABLE_HIKROBOT_MVS=ON `
  -DSEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR="C:/Program Files (x86)/MVS/Development/Includes" `
  -DSEAT_AOI_HIKROBOT_MVS_LIBRARY="C:/Program Files (x86)/MVS/Development/Libraries/win64/MvCameraControl.lib"
cmake --build build --config Release
```

### Python 环境初始化

有网环境可以直接按锁文件恢复：

```powershell
uv sync
```

工控机无公网时，先解压项目部署包和 Python 离线依赖包，再在项目目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\offline_python_deps\install_offline.ps1 -ProjectRoot .
.\.venv\Scripts\python.exe -m tools.validate_deployment_preflight
```

不要把开发机当前 `.venv/` 直接复制到工控机；依赖应通过离线 `wheelhouse/` 在目标机重建。

---

## 七、工控机启动流程

### 上线前最后检查

```powershell
# 1. 校验 C++ 生产配置（应全部通过，无 TODO）
.\bin\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.production.conf --validate-config

# 2. 校验 Python 侧架构就绪度
uv run python -m tools.validate_architecture_readiness --scope production

# 3. 校验模型资产
uv run python -m tools.validate_model_assets --recipe seat_a_black_leather_production_v1

# 4. 校验部署预检
uv run python -m tools.validate_deployment_preflight --strict-production
```

### 生产启动（三个终端）

```powershell
# ====== 终端 1：Python 检测进程 ======
uv run python -m python_detector.detector_main --config cpp_controller/config/station_runtime.production.conf

# ====== 终端 2：C++ 主控（持续循环模式）=======
.\bin\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.production.conf --loop

# ====== 终端 3：展示前端 ======
uv run seat-aoi-display --trace-root trace --line-id LINE1_AOI_01 --grid-layout 2x1
```

### 实验室联调启动（无外部信号，手动触发）

C++ 用 `station_runtime.test.conf` 替代 `production.conf`（已预置好序列号）：

```powershell
# 终端 2 改为：
.\bin\seat_aoi_controller.exe --config cpp_controller\config\station_runtime.test.conf --once
```

每次运行处理一个手动触发，不需要外部信号。

---

## 八、待完成时间线

| 阶段 | 内容 | 阻塞性 |
|------|------|--------|
| **现在（工控机组装）** | 按接线图连接硬件，确认 COM 口、相机在线 | 🔴 阻塞 |
| **现在（配置确认）** | 填入真实 COM 口，确认结果回传端 IP | 🔴 阻塞 |
| **联调阶段** | `lab_manual.conf` 手动触发，验证相机采图、频闪、共享内存、Python 收图、display_app 展示 | 🔴 阻塞 |
| **联调阶段** | 确认触发端 IP，填入 `result_host` | 🟡 可后补 |
| **模型阶段** | 真实模型已补齐；上线前继续用现场 OK/NG/RECHECK 样本做阈值曲线、误检/漏检和分层验收 | 🔴 放行门禁 |
| **量产阶段** | 补齐标定对齐矩阵，压测节拍稳定性 | 🟡 迭代 |
| **量产阶段** | MES/报警/监控平台对接 | 🟡 扩展 |
