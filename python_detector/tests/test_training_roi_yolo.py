from __future__ import annotations

from pathlib import Path

import pytest

# 检查训练依赖是否可用
ultralytics_available = False
try:
    import ultralytics  # type: ignore  # noqa: F401
    import torch  # type: ignore  # noqa: F401
    import onnx  # type: ignore  # noqa: F401
    ultralytics_available = True
except Exception:
    pass


@pytest.fixture
def yolo_dataset(tmp_path: Path) -> Path:
    """构造最小 YOLO 数据集：10 张假图像 + 对应标注。"""
    ds_root = tmp_path / "yolo_ds"
    for subdir in ["images/train", "images/val", "labels/train", "labels/val"]:
        (ds_root / subdir).mkdir(parents=True, exist_ok=True)

    import numpy as np
    from PIL import Image

    for idx in range(10):
        img = Image.fromarray(
            np.random.randint(0, 255, (64, 64), dtype=np.uint8), mode="L"
        ).convert("RGB")
        split = "train" if idx < 7 else "val"
        img.save(ds_root / "images" / split / f"img_{idx:04d}.png")

    for idx in range(7):
        label = "0 0.35 0.35 0.65 0.35 0.65 0.65 0.35 0.65\n"
        (ds_root / "labels" / "train" / f"img_{idx:04d}.txt").write_text(label)
    for idx in range(7, 10):
        label = "0 0.35 0.35 0.65 0.35 0.65 0.65 0.35 0.65\n"
        (ds_root / "labels" / "val" / f"img_{idx:04d}.txt").write_text(label)

    yaml_content = f"""path: {ds_root}
train: images/train
val: images/val
names:
  0: seat
"""
    (ds_root / "dataset.yaml").write_text(yaml_content)
    return ds_root / "dataset.yaml"


@pytest.mark.skipif(not ultralytics_available, reason="ultralytics/torch/onnx 未安装")
def test_train_yolo_minimal(tmp_path: Path, yolo_dataset: Path) -> None:
    """用合成数据训练 1 epoch，验证 ONNX 导出成功。"""
    from training_tools.train_roi_yolo import train_roi_yolo

    output = tmp_path / "roi_yolo.onnx"
    result = train_roi_yolo(
        data_path=yolo_dataset,
        model="yolov8n-seg.pt",
        task="segment",
        epochs=1,
        imgsz=64,
        batch=2,
        output=output,
        opset=17,
    )

    assert output.exists()
    assert output.stat().st_size > 1
    assert "mAP50" in result


@pytest.mark.skipif(not ultralytics_available, reason="ultralytics/torch/onnx 未安装")
def test_train_yolo_onnx_valid(tmp_path: Path, yolo_dataset: Path) -> None:
    """验证导出的 ONNX 文件可通过 onnx.checker 验证。"""
    import onnx as _onnx  # type: ignore

    from training_tools.train_roi_yolo import train_roi_yolo

    output = tmp_path / "roi_yolo.onnx"
    train_roi_yolo(
        data_path=yolo_dataset,
        model="yolov8n-seg.pt",
        task="segment",
        epochs=1,
        imgsz=64,
        batch=2,
        output=output,
        opset=17,
    )

    model = _onnx.load(str(output))
    _onnx.checker.check_model(model)


def test_train_supervised_yolo_delegates_to_shared_export(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """监督缺陷 YOLO 训练入口复用同一套 ONNX 导出逻辑，但默认输出到 supervised_defect。"""
    from training_tools import train_supervised_yolo

    data = tmp_path / "dataset.yaml"
    data.write_text("names: {0: scratch}\n", encoding="utf-8")
    output = tmp_path / "seat_defect_detector.onnx"
    calls = {}

    def fake_train_roi_yolo(**kwargs):
        calls.update(kwargs)
        return {"metrics/mAP50(B)": 0.7}

    monkeypatch.setattr(train_supervised_yolo, "train_roi_yolo", fake_train_roi_yolo)

    metrics = train_supervised_yolo.train_supervised_yolo(
        data_path=data,
        model="yolov8n.pt",
        epochs=2,
        imgsz=128,
        batch=4,
        output=output,
        opset=17,
    )

    assert metrics["metrics/mAP50(B)"] == 0.7
    assert calls["data_path"] == data
    assert calls["output"] == output
    assert calls["task"] == "detect"


def test_export_wideresnet_embedding_reports_missing_dependencies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import builtins

    from training_tools.export_wideresnet_embedding import export_wideresnet_embedding
    from training_tools.training_errors import OnnxExportError

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name in {"onnx", "torch"} or name.startswith("torchvision"):
            raise ImportError("missing training dependency")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(OnnxExportError, match="导出依赖未安装"):
        export_wideresnet_embedding(tmp_path / "embedding.onnx")
