# 测试机集成清单

## 上机前检查

```bash
python3 -m pytest python_detector/tests
python3 -m tools.validate_protocol
bash tools/run_simulated_ipc.sh
```

## 上机联调顺序

1. 验证共享内存创建和清理。
2. 验证模拟链路 OK。
3. 接相机，保存每个机位和光源的图像元信息。
4. 接频闪，验证光源顺序和曝光。
5. 接 PLC 触发输入。
6. 接 PLC 输出。
7. 开启追溯，确认 NG/RECHECK/ERROR 文件完整。
8. 连续运行并记录节拍和异常码。

## 不允许跳过的失败测试

- detector 不启动时，C++ 必须 `RECHECK`。
- 模拟缺帧时，C++ 必须 `RECHECK`。
- 模拟光源故障时，C++ 必须 `RECHECK`。
- 配方错误、标定错误、模型错误不能输出 `OK`。

