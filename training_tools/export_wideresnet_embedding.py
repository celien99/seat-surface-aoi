from __future__ import annotations

import argparse
from pathlib import Path

from training_tools.training_errors import OnnxExportError


class _WideResNetEmbeddingExportError(OnnxExportError):
    pass


def export_wideresnet_embedding(
    output: Path,
    *,
    input_channels: int = 3,
    embedding_dim: int = 1024,
    input_height: int = 48,
    input_width: int = 64,
    checkpoint: Path | None = None,
    opset: int = 17,
) -> dict:
    """导出与 python_detector EmbeddingExtractor 兼容的 WideResNet50 ONNX embedding 模型。"""
    try:
        import onnx  # type: ignore
        import torch  # type: ignore
        from torch import nn  # type: ignore
        from torchvision.models import Wide_ResNet50_2_Weights, wide_resnet50_2  # type: ignore
    except Exception as exc:
        raise _WideResNetEmbeddingExportError(
            f"导出依赖未安装: {exc}. 安装: uv sync --group training"
        ) from exc

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
        if not checkpoint.exists():
            raise _WideResNetEmbeddingExportError(f"checkpoint 不存在: {checkpoint}")
        state = torch.load(str(checkpoint), map_location="cpu")
        if isinstance(state, dict) and "state_dict" in state:
            state = state["state_dict"]
        model.load_state_dict(state, strict=False)
    model.eval()

    dummy = torch.zeros(1, input_channels, input_height, input_width, dtype=torch.float32)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        dummy,
        str(output),
        input_names=["input"],
        output_names=["embedding"],
        dynamic_axes={
            "input": {0: "batch", 2: "height", 3: "width"},
            "embedding": {0: "batch"},
        },
        opset_version=opset,
    )
    if not output.exists() or output.stat().st_size <= 1:
        raise _WideResNetEmbeddingExportError(f"ONNX 导出文件无效: {output}")
    exported = onnx.load(str(output))
    onnx.checker.check_model(exported)
    return {
        "onnx_path": str(output),
        "input_channels": input_channels,
        "embedding_dim": embedding_dim,
        "input_height": input_height,
        "input_width": input_width,
        "opset": opset,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="导出 WideResNet50 embedding ONNX，供 PatchCore safety net 使用")
    parser.add_argument("--output", type=Path, default=Path("model/wideresnet50/seat_wrn50_embedding.onnx"))
    parser.add_argument("--input-channels", type=int, default=3)
    parser.add_argument("--embedding-dim", type=int, default=1024)
    parser.add_argument("--input-height", type=int, default=48)
    parser.add_argument("--input-width", type=int, default=64)
    parser.add_argument("--checkpoint", type=Path, default=None)
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args(argv)

    try:
        result = export_wideresnet_embedding(
            output=args.output,
            input_channels=args.input_channels,
            embedding_dim=args.embedding_dim,
            input_height=args.input_height,
            input_width=args.input_width,
            checkpoint=args.checkpoint,
            opset=args.opset,
        )
    except OnnxExportError as exc:
        print(f"export_wideresnet_embedding_failed={exc}")
        return 2
    print(
        f"onnx={result['onnx_path']} input_channels={result['input_channels']} "
        f"embedding_dim={result['embedding_dim']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
