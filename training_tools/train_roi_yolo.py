from __future__ import annotations

import argparse
from pathlib import Path

from training_tools.training_errors import OnnxExportError, TrainingDataError


def train_roi_yolo(
    data_path: Path,
    model: str = "yolov8n-seg.pt",
    *,
    task: str = "segment",
    epochs: int = 100,
    imgsz: int = 640,
    batch: int = 16,
    output: Path | None = None,
    opset: int = 17,
) -> dict:
    """训练 YOLO 模型并导出 ONNX。"""
    if task not in {"detect", "segment"}:
        raise TrainingDataError(f"不支持的 YOLO task: {task}")
    if not data_path.exists():
        raise TrainingDataError(f"数据集配置文件不存在: {data_path}")

    try:
        from ultralytics import YOLO  # type: ignore
        import onnx  # type: ignore
    except Exception as exc:
        raise OnnxExportError(f"训练依赖未安装: {exc}. 安装: uv sync --group training") from exc

    yolo_model = YOLO(model)
    results = yolo_model.train(
        data=str(data_path),
        task=task,
        epochs=epochs,
        imgsz=imgsz,
        batch=batch,
        verbose=False,
    )

    metrics = {}
    if hasattr(results, "results_dict"):
        metrics = {str(k): float(v) if isinstance(v, (int, float)) else v
                   for k, v in results.results_dict.items()}

    if output is not None:
        exported_path = yolo_model.export(format="onnx", opset=opset, imgsz=imgsz)
        exported = Path(exported_path) if not isinstance(exported_path, Path) else exported_path
        if not exported.exists() or exported.stat().st_size <= 1:
            raise OnnxExportError(f"ONNX 导出文件无效: {exported}")
        if exported != output:
            output.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.move(str(exported), str(output))
        returned_model = onnx.load(str(output))
        onnx.checker.check_model(returned_model)

    return metrics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="训练 Dome ROI YOLO 分割模型并导出 ONNX")
    parser.add_argument("--data", required=True, type=Path, help="YOLO 格式 dataset.yaml")
    parser.add_argument("--model", default="yolov8n-seg.pt", help="预训练权重或 checkpoint")
    parser.add_argument("--task", default="segment", choices=["detect", "segment"], help="ROI 训练任务类型")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--output", type=Path, default=Path("model/roi_yolo/seat_roi_seg.onnx"))
    parser.add_argument("--opset", type=int, default=17)
    args = parser.parse_args(argv)

    try:
        metrics = train_roi_yolo(
            data_path=args.data,
            model=args.model,
            task=args.task,
            epochs=args.epochs,
            imgsz=args.imgsz,
            batch=args.batch,
            output=args.output,
            opset=args.opset,
        )
    except (TrainingDataError, OnnxExportError) as exc:
        print(f"train_roi_yolo_failed={exc}")
        return 2

    print(f"onnx={args.output} mAP50={metrics.get('metrics/mAP50(B)', 'N/A')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
