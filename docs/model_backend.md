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
    model_family: supervised
    role: primary
  scratch_onnx:
    backend: onnx
    model_path: models/scratch.onnx
    model_family: supervised
    role: primary
  unknown_safety_net:
    backend: onnx
    model_path: models/patchcore_unknown.onnx
    model_family: patchcore
    role: safety_net
```

## 模型角色

- `primary`：ROI 主检测模型，用于已知缺陷的监督检测、分割、分类或 EfficientAD 等主流程。
- `safety_net`：未知缺陷安全网，用于 PatchCore 等异常检测模型。

`patchcore` 必须配置为 `role: safety_net`。配方 schema 会拒绝把 safety net 模型挂到 `model_key` 或 `roi_models` 主模型字段。

## 接入真实模型要求

- 明确输入尺寸、通道顺序、归一化方式。
- 明确输出 decode 规则、bbox 坐标格式和 mask 格式。
- 缺模型、后端异常、输出 decode 失败不能输出 `OK`。
- 每个 ROI 应明确主模型和可选安全网模型，不允许用单一 PatchCore 覆盖全座椅主检。
