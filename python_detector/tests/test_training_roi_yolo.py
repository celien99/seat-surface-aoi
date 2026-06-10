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
        label = "0 0.5 0.5 0.3 0.3\n"
        (ds_root / "labels" / "train" / f"img_{idx:04d}.txt").write_text(label)
    for idx in range(7, 10):
        label = "0 0.5 0.5 0.3 0.3\n"
        (ds_root / "labels" / "val" / f"img_{idx:04d}.txt").write_text(label)

    yaml_content = f"""path: {ds_root}
train: images/train
val: images/val
names:
  0: full
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
        model="yolov8n.pt",
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
        model="yolov8n.pt",
        epochs=1,
        imgsz=64,
        batch=2,
        output=output,
        opset=17,
    )

    model = _onnx.load(str(output))
    _onnx.checker.check_model(model)
