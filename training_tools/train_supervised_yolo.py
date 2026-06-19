from __future__ import annotations

import argparse
from pathlib import Path

from training_tools.train_roi_yolo import train_roi_yolo
from training_tools.training_errors import OnnxExportError, TrainingDataError


def train_supervised_yolo(
    data_path: Path,
    model: str = "yolov8n.pt",
    *,
    epochs: int = 100,
    imgsz: int = 640,
    batch: int = 16,
    output: Path | None = None,
    opset: int = 17,
) -> dict:
    """训练已知缺陷监督检测 YOLO 并导出 ONNX。"""
    return train_roi_yolo(
        data_path=data_path,
        model=model,
        task="detect",
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        output=output,
        opset=opset,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="训练已知缺陷监督检测 YOLO 模型并导出 ONNX")
    parser.add_argument("--data", required=True, type=Path, help="YOLO 格式 dataset.yaml")
    parser.add_argument("--model", default="yolov8n.pt", help="预训练权重或 checkpoint")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--output", type=Path, default=Path("model/supervised_defect/seat_defect_detector.onnx"))
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args(argv)

    try:
        metrics = train_supervised_yolo(
            data_path=args.data,
            model=args.model,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            output=args.output,
            opset=args.opset,
        )
    except (TrainingDataError, OnnxExportError) as exc:
        print(f"train_supervised_yolo_failed={exc}")
        return 2

    print(f"onnx={args.output} mAP50={metrics.get('metrics/mAP50(B)', 'N/A')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
