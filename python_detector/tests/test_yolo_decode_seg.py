"""YOLO 分割解码专项测试。"""

import numpy as np
import pytest

from python_detector.models.yolo_decode import (
    SegmentationCandidate,
    decode_yolo_rows,
    decode_yolo_segmentation,
)


class TestYoloDetectionDecode:
    """检测行解码。"""

    def test_decode_yolo_xyxy_rows_single(self):
        rows = np.array([[10.0, 20.0, 30.0, 40.0, 0.85, 0.0]], dtype=np.float32)
        result = decode_yolo_rows(rows, confidence_threshold=0.5, output_decode="yolo_xyxy_rows")
        assert len(result) == 1
        assert result[0][4] == pytest.approx(0.85)

    def test_decode_keeps_all_rows_regardless_of_confidence(self):
        """decode_yolo_rows 不自行过滤置信度——过滤由调用方完成。"""
        rows = np.array([
            [10.0, 20.0, 30.0, 40.0, 0.85, 0.0],
            [50.0, 60.0, 70.0, 80.0, 0.35, 1.0],
        ], dtype=np.float32)
        result = decode_yolo_rows(rows, confidence_threshold=0.5, output_decode="yolo_xyxy_rows")
        assert len(result) == 2

    def test_decode_empty_rows(self):
        rows = np.zeros((0, 6), dtype=np.float32)
        result = decode_yolo_rows(rows, confidence_threshold=0.5, output_decode="yolo_xyxy_rows")
        assert len(result) == 0

    def test_decode_invalid_format_raises(self):
        with pytest.raises(RuntimeError, match="不支持的 YOLO 输出解码方式"):
            decode_yolo_rows(np.zeros((1, 6)), confidence_threshold=0.5, output_decode="unsupported")


class TestSegmentationDecode:
    """分割行解码。"""

    def test_ultralytics_seg_decode(self):
        """Ultralytics YOLOv8-seg 格式 (N=3, D=37=4+1+32)。"""
        D, N = 37, 3
        boxes = np.zeros((D, N), dtype=np.float32)
        boxes[0, 0] = 0.5; boxes[0, 1] = 0.5; boxes[0, 2] = 0.5  # cx
        boxes[1, 0] = 0.5; boxes[1, 1] = 0.5; boxes[1, 2] = 0.5  # cy
        boxes[2, 0] = 0.1; boxes[2, 1] = 0.1; boxes[2, 2] = 0.1  # w
        boxes[3, 0] = 0.1; boxes[3, 1] = 0.1; boxes[3, 2] = 0.1  # h
        boxes[4, :] = 0.9  # class 0 score
        boxes[5:, :] = np.linspace(-0.1, 0.1, 32)[:, None]
        protos = np.random.randn(1, 32, 16, 16).astype(np.float32) * 0.01

        result = decode_yolo_segmentation(
            [boxes, protos],
            confidence_threshold=0.5, mask_threshold=0.5, output_decode="ultralytics_yolo_seg",
        )
        assert len(result) >= 1
        assert result[0].score == pytest.approx(0.9)

    def test_empty_outputs_raises(self):
        with pytest.raises(RuntimeError, match="YOLO segmentation 输出为空"):
            decode_yolo_segmentation(
                [],
                confidence_threshold=0.5, mask_threshold=0.5, output_decode="segmentation_rows",
            )

    def test_ultralytics_missing_protos_raises(self):
        with pytest.raises(RuntimeError, match="至少需要"):
            decode_yolo_segmentation(
                [np.zeros((37, 3), dtype=np.float32)],
                confidence_threshold=0.5, mask_threshold=0.5, output_decode="ultralytics_yolo_seg",
            )


class TestSegmentationCandidate:
    """SegmentationCandidate 数据类字段。"""

    def test_candidate_minimal(self):
        c = SegmentationCandidate(
            bbox_xyxy=(10.0, 20.0, 30.0, 40.0),
            score=0.88,
            class_id=0,
            mask=np.ones((32, 32), dtype=np.float32),
        )
        assert c.score == 0.88
        assert c.class_id == 0
        assert c.mask.shape == (32, 32)
        assert c.bbox_xyxy == (10.0, 20.0, 30.0, 40.0)
