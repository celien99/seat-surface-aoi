"""ROI Locator 掩码处理专项测试：NaN 防御、上采样、二值化、侵蚀。"""

import numpy as np
import pytest

from python_detector.pipeline.roi_locator import _erode_output_mask_1px


class TestMaskNaNGuard:
    """验证 ONNX 输出 NaN 时不会静默传播。"""

    def test_nan_in_mask_raises(self):
        from python_detector.pipeline.roi_locator import RoiLocator
        locator = RoiLocator()
        mask = np.zeros((32, 32), dtype=np.float32)
        mask[10:20, 12:18] = 1.0
        mask[15, 15] = np.nan
        with pytest.raises(RuntimeError, match="非有限值"):
            locator._mask_to_roi_mask(mask, (12, 10, 18, 20), (64, 64))

    def test_inf_in_mask_raises(self):
        from python_detector.pipeline.roi_locator import RoiLocator
        locator = RoiLocator()
        mask = np.zeros((32, 32), dtype=np.float32)
        mask[10:20, 12:18] = 1.0
        mask[10, 12] = np.inf
        with pytest.raises(RuntimeError, match="非有限值"):
            locator._mask_to_roi_mask(mask, (12, 10, 18, 20), (64, 64))

    def test_valid_mask_passes(self):
        from python_detector.pipeline.roi_locator import RoiLocator
        locator = RoiLocator()
        mask = np.zeros((32, 32), dtype=np.float32)
        mask[10:20, 12:18] = 1.0
        result = locator._mask_to_roi_mask(mask, (12, 10, 18, 20), (64, 64))
        assert result.width == 64
        assert result.height == 64
        assert len(result.pixels) == 64 * 64
        roi = np.frombuffer(result.pixels, dtype=np.uint8).reshape(64, 64)
        assert roi.sum() > 0


class TestMaskErosion:
    """1px 掩码腐蚀测试。"""

    def test_erosion_removes_isolated_pixel(self):
        """孤立单像素应被 4 邻域腐蚀移除。"""
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[8, 8] = 255
        eroded = _erode_output_mask_1px(mask, np)
        assert eroded[8 * 16 + 8] == 0  # 线性索引

    def test_erosion_preserves_solid_region(self):
        """3×3 以上实心区域经 1px 腐蚀后中心应保留。"""
        mask = np.zeros((16, 16), dtype=np.uint8)
        mask[6:10, 6:10] = 255
        eroded = _erode_output_mask_1px(mask, np)
        assert any(b != 0 for b in eroded)  # 至少保留部分像素

    def test_erosion_empty_mask(self):
        """全零掩码经腐蚀后仍为全零。"""
        mask = np.zeros((16, 16), dtype=np.uint8)
        eroded = _erode_output_mask_1px(mask, np)
        assert all(b == 0 for b in eroded)
