from __future__ import annotations

import argparse
from pathlib import Path

from training_tools.training_errors import OnnxExportError


class _WideResNetEmbeddingExportError(OnnxExportError):
    pass


def export_wideresnet_embedding(
    output: Path,
    *,
    input_channels: int,
    embedding_dim: int = 1024,
    input_height: int = 48,
    input_width: int = 64,
    checkpoint: Path | None = None,
    opset: int = 17,
    spatial_mode: bool = False,
    spatial_layers: tuple[str, ...] = (),
) -> dict:
    """导出与 python_detector EmbeddingExtractor 兼容的 WideResNet50 ONNX embedding 模型。

    当 spatial_mode=False 时，导出全局嵌入模型（含 GAP），输出 [B, embedding_dim]。
    当 spatial_mode=True 时，导出空间特征模型（不含 GAP），输出各中间层特征图 [B, C_i, H_i, W_i]。
    """
    if input_channels <= 0:
        raise _WideResNetEmbeddingExportError("--input-channels 必须是正整数")
    if spatial_mode and not spatial_layers:
        raise _WideResNetEmbeddingExportError("spatial_mode 必须指定 --spatial-layers")
    try:
        import onnx  # type: ignore
        import onnxscript  # type: ignore  # noqa: F401
        import torch  # type: ignore
        from torch import nn  # type: ignore
        from torchvision.models import Wide_ResNet50_2_Weights, wide_resnet50_2  # type: ignore
    except Exception as exc:
        raise _WideResNetEmbeddingExportError(
            f"导出依赖未安装: {exc}. 安装: uv sync --group training"
        ) from exc

    if spatial_mode:
        model = _build_spatial_model(input_channels, spatial_layers, checkpoint)
    else:
        model = _build_global_model(input_channels, embedding_dim, checkpoint)
    model.eval()

    dummy = torch.zeros(1, input_channels, input_height, input_width, dtype=torch.float32)
    output.parent.mkdir(parents=True, exist_ok=True)

    if spatial_mode:
        output_names = list(spatial_layers)
        dynamic_axes = {"input": {0: "batch", 2: "height", 3: "width"}}
        for name in spatial_layers:
            dynamic_axes[name] = {0: "batch", 2: "height", 3: "width"}
    else:
        output_names = ["embedding"]
        dynamic_axes = {
            "input": {0: "batch", 2: "height", 3: "width"},
            "embedding": {0: "batch"},
        }

    try:
        torch.onnx.export(
            model,
            dummy,
            str(output),
            input_names=["input"],
            output_names=output_names,
            dynamic_axes=dynamic_axes,
            opset_version=opset,
            dynamo=False,
        )
    except ModuleNotFoundError as exc:
        raise _WideResNetEmbeddingExportError(
            f"ONNX 导出依赖缺失: {exc.name}. 安装: uv sync --group training"
        ) from exc
    if not output.exists() or output.stat().st_size <= 1:
        raise _WideResNetEmbeddingExportError(f"ONNX 导出文件无效: {output}")
    exported = onnx.load(str(output))
    onnx.checker.check_model(exported)
    result: dict = {
        "onnx_path": str(output),
        "input_channels": input_channels,
        "input_height": input_height,
        "input_width": input_width,
        "opset": opset,
        "spatial_mode": spatial_mode,
    }
    if spatial_mode:
        result["spatial_layers"] = list(spatial_layers)
        result["output_names"] = output_names
    else:
        result["embedding_dim"] = embedding_dim
    return result


def _build_global_model(
    input_channels: int,
    embedding_dim: int,
    checkpoint: Path | None,
) -> "torch.nn.Module":  # type: ignore[name-defined]
    import torch  # type: ignore
    from torch import nn  # type: ignore
    from torchvision.models import Wide_ResNet50_2_Weights, wide_resnet50_2  # type: ignore

    class WideResNetEmbedding(nn.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            backbone = wide_resnet50_2(weights=Wide_ResNet50_2_Weights.DEFAULT)
            first_conv = backbone.conv1
            if input_channels != int(first_conv.in_channels):
                adapted = nn.Conv2d(
                    input_channels,
                    first_conv.out_channels,
                    kernel_size=first_conv.kernel_size,
                    stride=first_conv.stride,
                    padding=first_conv.padding,
                    bias=first_conv.bias is not None,
                )
                with torch.no_grad():
                    source_weight = first_conv.weight
                    if input_channels == 1:
                        adapted.weight.copy_(source_weight.mean(dim=1, keepdim=True))
                    else:
                        repeated = source_weight.mean(dim=1, keepdim=True).repeat(1, input_channels, 1, 1)
                        adapted.weight.copy_(repeated)
                    if first_conv.bias is not None and adapted.bias is not None:
                        adapted.bias.copy_(first_conv.bias)
                backbone.conv1 = adapted
            backbone.fc = nn.Identity()
            self.backbone = backbone
            self.projection = nn.Linear(2048, embedding_dim)

        def forward(self, tensor):  # type: ignore[no-untyped-def]
            features = self.backbone(tensor)
            return self.projection(features)

    model = WideResNetEmbedding()
    if checkpoint is not None:
        _load_checkpoint(model, checkpoint)
    return model


def _build_spatial_model(
    input_channels: int,
    spatial_layers: tuple[str, ...],
    checkpoint: Path | None,
) -> "torch.nn.Module":  # type: ignore[name-defined]
    import torch  # type: ignore
    from torch import nn  # type: ignore
    from torchvision.models import Wide_ResNet50_2_Weights, wide_resnet50_2  # type: ignore

    valid_layers = frozenset({"layer1", "layer2", "layer3", "layer4"})
    for name in spatial_layers:
        if name not in valid_layers:
            raise _WideResNetEmbeddingExportError(
                f"spatial_layers 必须是 {sorted(valid_layers)} 之一: {name}"
            )

    class WideResNetSpatialEmbedding(nn.Module):  # type: ignore[misc]
        def __init__(self) -> None:
            super().__init__()
            backbone = wide_resnet50_2(weights=Wide_ResNet50_2_Weights.DEFAULT)
            first_conv = backbone.conv1
            if input_channels != int(first_conv.in_channels):
                adapted = nn.Conv2d(
                    input_channels,
                    first_conv.out_channels,
                    kernel_size=first_conv.kernel_size,
                    stride=first_conv.stride,
                    padding=first_conv.padding,
                    bias=first_conv.bias is not None,
                )
                with torch.no_grad():
                    source_weight = first_conv.weight
                    if input_channels == 1:
                        adapted.weight.copy_(source_weight.mean(dim=1, keepdim=True))
                    else:
                        repeated = source_weight.mean(dim=1, keepdim=True).repeat(1, input_channels, 1, 1)
                        adapted.weight.copy_(repeated)
                    if first_conv.bias is not None and adapted.bias is not None:
                        adapted.bias.copy_(first_conv.bias)
                backbone.conv1 = adapted
            self.conv1 = backbone.conv1
            self.bn1 = backbone.bn1
            self.relu = backbone.relu
            self.maxpool = backbone.maxpool
            self.layer1 = backbone.layer1
            self.layer2 = backbone.layer2
            self.layer3 = backbone.layer3
            self.layer4 = backbone.layer4
            self.layer_names = list(spatial_layers)

        def forward(self, x):  # type: ignore[no-untyped-def]
            x = self.conv1(x)
            x = self.bn1(x)
            x = self.relu(x)
            x = self.maxpool(x)
            outputs: dict[str, "torch.Tensor"] = {}
            x = self.layer1(x)
            if "layer1" in self.layer_names:
                outputs["layer1"] = x
            x = self.layer2(x)
            if "layer2" in self.layer_names:
                outputs["layer2"] = x
            x = self.layer3(x)
            if "layer3" in self.layer_names:
                outputs["layer3"] = x
            x = self.layer4(x)
            if "layer4" in self.layer_names:
                outputs["layer4"] = x
            return tuple(outputs[name] for name in self.layer_names)

    model = WideResNetSpatialEmbedding()
    if checkpoint is not None:
        _load_checkpoint(model, checkpoint)
    return model


def _load_checkpoint(model: "torch.nn.Module", checkpoint: Path) -> None:  # type: ignore[name-defined]
    import torch  # type: ignore
    if not checkpoint.exists():
        raise _WideResNetEmbeddingExportError(f"checkpoint 不存在: {checkpoint}")
    state = torch.load(str(checkpoint), map_location="cpu")
    if isinstance(state, dict) and "state_dict" in state:
        state = state["state_dict"]
    model.load_state_dict(state, strict=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 WideResNet50 embedding ONNX，供 PatchCore safety net 使用")
    parser.add_argument("--output", type=Path, default=Path("model/wideresnet50/seat_wrn50_embedding.onnx"))
    parser.add_argument("--input-channels", type=int, required=True, help="模型输入通道数，必须与配方 models.<key>.input_channels 数量一致")
    parser.add_argument("--embedding-dim", type=int, default=1024)
    parser.add_argument("--input-height", type=int, default=48)
    parser.add_argument("--input-width", type=int, default=64)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--opset", type=int, default=17)
    parser.add_argument("--spatial-mode", action="store_true", help="导出空间特征模型（不含 GAP），输出中间层特征图")
    parser.add_argument("--spatial-layers", default="layer2,layer3", help="空间模式下导出的中间层，逗号分隔，默认 layer2,layer3")
    args = parser.parse_args(argv)

    spatial_layers: tuple[str, ...] = ()
    if args.spatial_mode:
        spatial_layers = tuple(layer.strip() for layer in args.spatial_layers.split(",") if layer.strip())

    try:
        result = export_wideresnet_embedding(
            output=args.output,
            input_channels=args.input_channels,
            embedding_dim=args.embedding_dim,
            input_height=args.input_height,
            input_width=args.input_width,
            checkpoint=args.checkpoint,
            opset=args.opset,
            spatial_mode=args.spatial_mode,
            spatial_layers=spatial_layers,
        )
    except OnnxExportError as exc:
        print(f"export_wideresnet_embedding_failed={exc}")
        return 2
    if result.get("spatial_mode"):
        print(
            f"onnx={result['onnx_path']} input_channels={result['input_channels']} "
            f"spatial_layers={result['spatial_layers']} spatial_mode=true"
        )
    else:
        print(
            f"onnx={result['onnx_path']} input_channels={result['input_channels']} "
            f"embedding_dim={result['embedding_dim']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
