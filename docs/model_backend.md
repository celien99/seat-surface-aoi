# 模型后端说明

## 当前后端

- `fake`：默认后端，用于模拟 OK、RECHECK、NG 分支。
- `onnx`：可选后端。未安装 `onnxruntime`、模型路径为空、模型文件不存在或输出解码未配置时，返回保守错误。

## 配方示例

```yaml
models:
  fake_default:
    backend: fake
    fake_mode: auto
  scratch_onnx:
    backend: onnx
    model_path: models/scratch.onnx
```

## 接入真实模型要求

- 明确输入尺寸、通道顺序、归一化方式。
- 明确输出 decode 规则、bbox 坐标格式和 mask 格式。
- 缺模型、后端异常、输出 decode 失败不能输出 `OK`。

