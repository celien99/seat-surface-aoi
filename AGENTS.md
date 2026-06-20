# AGENTS.md

## 项目定位

本项目是汽车座椅表面缺陷检测系统的参考实现，在线主链路遵循 `docs/v4_architecture_alignment.md`、`docs/shm_protocol.md` 和 `docs/README.md`：

- C++ 负责 PLC、相机、频闪、共享内存写入、结果读取和节拍控制。
- Python 作为独立检测进程，负责图像质量门禁、预处理、多光源特征、模型推理、融合和规则判定。
- C++ 与 Python 在线交换图像和结果必须使用共享内存，不使用 TCP。

## 必须遵守的工作规则

1. 每一次新增代码、修改代码、修复代码都必须形成 Git commit 提交记录，并符合提交规范。
2. 任何代码变更都必须同步更新根目录 `README.md`, 同步可以变动的功能和文件结构。
3. 文档、注释、提交说明和面向用户的说明优先使用中文。
4. 不允许用 Python 控制 PLC、相机或频闪；Python 只负责检测链路。
5. 不允许在 C++ 主控中实现深度学习推理；模型和算法逻辑属于 Python 检测进程。
6. 任何不确定状态、超时、缺帧、协议错误、CRC 错误、质量门禁失败都不能输出 `OK`，必须输出 `RECHECK` 或 `ERROR`。
7. 修改共享内存协议时，必须同步更新 C++ 与 Python 两侧结构、协议校验工具和相关测试。
8. 优先寻找适合的skills进行开发，例如开发C++模块可以使用 Seat Inspection C++ Strobe Controller; 开发Python程序利用 Seat Inspection Python Detector和 Seat Inspection Visiion Algorithm
9. 开发思维要具备模块化思维，避免耦合度过高以及代码冗余，性能优先原则
10. 对 `python_detector` 进行新增、修改、修复或重构时，必须同步更新 `python_detector/README.md`，确保 Python 算法层文件结构、实现内容、作用和验证方式保持最新。

## 推荐执行顺序

1. 先阅读 `docs/README.md`、`docs/v4_architecture_alignment.md`、`docs/shm_protocol.md` 和本文件。
2. 检查当前 git 状态，确认是否存在用户未提交改动。
3. 按最小可验证范围修改代码。
4. 同步更新 `README.md`；如涉及 `python_detector`，同步更新 `python_detector/README.md`, 涉及`cpp_controller`,同步更新 `cpp_controller/README.md`。
5. 运行相关验证命令。
6. 提交 commit，并在提交信息中说明变更范围和验证结果, 并符合提交规范。

## 常用验证命令

```powershell
uv run pytest
uv run python -m tools.validate_protocol
uv run python tools/run_simulated_ipc.py
```

## Git 提交规范 (Git Commit Guidelines)

当需要提交代码时，必须严格遵守约定式提交（Conventional Commits）与 **Gitmoji** 结合的规范。每次执行 `git commit` 时，提交信息必须采用以下格式：

### 1. 允许的提交前缀与 Emoji 对应表 (Types & Emojis)

- **feat**: ✨ 引入新的功能或特性。
- **fix**: 🐛 修复已知的 Bug 或缺陷。
- **chore**: 🔧 构建过程、辅助工具、依赖库更新或日常行政变动（如更新 .gitignore）。
- **refactor**: ♻️ 代码重构，既没有修复 Bug 也没有添加新功能。
- **docs**: 📝 仅修改文档或注释（如 README.md, API说明）。
- **style**: 💄 不影响代码含义的更改（如消除空格、格式化、补全缺失的分号）。
- **perf**: ⚡ 提升系统性能或优化运行效率的代码修改。
- **test**: ✅ 增加、修改或完善测试用例。
- **ci**: 💚 针对 CI/CD 配置文件和脚本的更改（如 GitHub Actions 流程调整）。

### 2. 约束细节 (Constraints)

- **格式要求**：Emoji 必须紧跟在冒号和空格后面，并且 **Emoji 与后面的文字描述之间必须保留一个空格**（例如：`: ✨ 增加...`）。
- **直接使用字符**：请直接输入 Emoji 图标本身（如 ✨），不需要输入冒号代码（如 `:sparkles:`）。
- **严格匹配**：严禁自行发明或使用上述表格以外的任何其他 Emoji 表情。

### 3. 示例 (Examples)

- `feat(user): ✨ 增加用户头像上传功能`
- `fix(cart): 🐛 修复购物车结算时金额计算精度丢失的问题`
- `docs: 📝 补充 AGENTS.md 中的 Git 提交规范说明`
- `feat(user): ✨ 增加用户头像上传功能`
- `fix(cart): 🐛 修复购物车结算时金额计算精度丢失的问题`
- `docs: 📝 补充 AGENTS.md 中的 Git 提交规范说明`
- `chore: 🔧 升级 tailwindcss 依赖到最新版本`

如果本机没有 `cmake`，`run_simulated_ipc.py` 会自动回退到 `clang++` 编译 C++ 主控。
