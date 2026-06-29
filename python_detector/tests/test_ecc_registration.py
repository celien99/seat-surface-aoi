"""ECC 配准模块专项测试。"""

import types

import numpy as np

from python_detector.ipc.data_types import LightFrame
from python_detector.pipeline.ecc_registration import EccRegistration


def _make_frame(light_id: str, width: int = 128, height: int = 96, shift_x: int = 0, shift_y: int = 0) -> LightFrame:
    pixels = np.zeros((height, width), dtype=np.uint8)
    pixels[20:80, 30:100] = 200
    pixels[40:60, 50:80] = 120
    if shift_x != 0 or shift_y != 0:
        pixels = np.roll(np.roll(pixels, shift_x, axis=1), shift_y, axis=0)
    return LightFrame(
        camera_id="TEST", light_id=light_id, frame_index=0, light_seq_index=0,
        width=width, height=height, channels=1, stride_bytes=width,
        pixel_format="MONO8", bit_depth=8, color_order="GRAY", dtype="UINT8",
        timestamp_us=1000, exposure_us=30000, gain=1.0,
        calibration_id="calib/test_v1", image_crc32=0,
        image=memoryview(bytearray(pixels.tobytes())),
        origin_xy=(0, 0), source_width=width, source_height=height,
    )


class TestEccAlignment:
    """ECC 端到端配准测试。"""

    def test_self_alignment_zero_shift(self):
        """同一帧自配准：零偏移高相关性。"""
        ecc = EccRegistration()
        frame = _make_frame("L1")
        result = ecc.align_translation(
            frame, frame,
            search_radius_px=3, max_iterations=100,
            convergence_epsilon=1e-6, min_correlation=0.8,
        )
        assert result.shift_xy == (0, 0)
        assert result.correlation >= 0.8

    def test_known_translation_recovery(self):
        """基准帧自配准已知平移帧（右移 3px）应恢复。"""
        ecc = EccRegistration()
        base = _make_frame("base")
        moving = _make_frame("moving", shift_x=3)
        result = ecc.align_translation(
            base, moving,
            search_radius_px=5, max_iterations=200,
            convergence_epsilon=1e-6, min_correlation=0.7,
        )
        assert result.shift_xy[0] == 3  # x exactly recoverable
        assert result.converged

    def test_size_mismatch_returns_failure(self):
        ecc = EccRegistration()
        base = _make_frame("base", 128, 96)
        moving = _make_frame("moving", 64, 48)
        result = ecc.align_translation(
            base, moving,
            search_radius_px=3, max_iterations=30,
            convergence_epsilon=1e-6, min_correlation=0.5,
        )
        assert not result.converged
        assert "尺寸不一致" in result.message

    def test_apply_translation_noop(self):
        ecc = EccRegistration()
        frame = _make_frame("L1")
        aligned = ecc.apply_translation(frame, (0, 0))
        assert aligned is frame

    def test_apply_translation_content(self):
        ecc = EccRegistration()
        frame = _make_frame("L1")
        aligned = ecc.apply_translation(frame, (2, 0))
        assert aligned.width == frame.width
        src = np.frombuffer(frame.image, dtype=np.uint8).reshape(frame.height, frame.width)
        dst = np.frombuffer(aligned.image, dtype=np.uint8).reshape(frame.height, frame.width)
        np.testing.assert_array_equal(dst[:, 0], src[:, 0])

    def test_translation_matrix_format(self):
        ecc = EccRegistration()
        m = ecc._translation_matrix(3, -2)
        assert len(m) == 9
        assert m[0] == 1.0 and m[4] == 1.0 and m[8] == 1.0
        assert m[2] == 3.0
        assert m[5] == -2.0

    def test_opencv_ecc_uses_returned_correlation_and_warp(self, monkeypatch):
        """OpenCV findTransformECC 返回 (correlation, warpMatrix)，低相关性必须 RECHECK。"""
        warp = np.array([[1.0, 0.0, 4.0], [0.0, 1.0, -2.0]], dtype=np.float32)

        fake_cv2 = types.SimpleNamespace(
            TERM_CRITERIA_EPS=1,
            TERM_CRITERIA_COUNT=2,
            MOTION_TRANSLATION=3,
            error=RuntimeError,
            findTransformECC=lambda *args: (0.25, warp),
        )
        monkeypatch.setitem(__import__("sys").modules, "cv2", fake_cv2)

        ecc = EccRegistration()
        frame = _make_frame("L1")
        array = np.frombuffer(frame.image, dtype=np.uint8).reshape(frame.height, frame.width).astype(np.float32)

        result = ecc._ecc_opencv(  # noqa: SLF001
            array,
            array,
            "L1",
            max_iterations=30,
            convergence_epsilon=1e-6,
            min_correlation=0.8,
        )

        assert result is not None
        assert result.correlation == 0.25
        assert result.shift_xy == (4, -2)
        assert result.converged is False


class TestEccExhaustive:
    """暴力搜索回退方案。"""

    def test_self_correlation_max(self):
        ecc = EccRegistration()
        frame = _make_frame("L1")
        arr = np.frombuffer(frame.image, dtype=np.uint8).reshape(frame.height, frame.width).astype(np.float32)
        result = ecc._ecc_exhaustive(
            arr, arr, "L1",
            search_radius_px=3, max_iterations=100,
            convergence_epsilon=1e-6, min_correlation=0.9,
        )
        assert result.shift_xy == (0, 0)
        assert result.converged

    def test_radius_hard_cap(self):
        """搜索半径超过硬上限应截断为 10，不 O(R²) 爆炸。"""
        ecc = EccRegistration()
        frame = _make_frame("L1")
        arr = np.frombuffer(frame.image, dtype=np.uint8).reshape(frame.height, frame.width).astype(np.float32)
        result = ecc._ecc_exhaustive(
            arr, arr, "L1",
            search_radius_px=50, max_iterations=500,
            convergence_epsilon=1e-6, min_correlation=0.9,
        )
        # 被截断为半径 10：最多 1 + 4 + 8 + ... + 20 = 221 次迭代
        assert result.iterations < 300

    def test_iteration_cap_enforced(self):
        ecc = EccRegistration()
        frame = _make_frame("L1")
        arr = np.frombuffer(frame.image, dtype=np.uint8).reshape(frame.height, frame.width).astype(np.float32)
        result = ecc._ecc_exhaustive(
            arr, arr, "L1",
            search_radius_px=5, max_iterations=5,
            convergence_epsilon=0.0, min_correlation=0.9,
        )
        assert result.iterations <= 5
