from __future__ import annotations

from pathlib import Path

import numpy as np

from python_detector.image_codec import ImageCodecError, load_raster_image


class RasterImageError(ValueError):
    """展示图像解码失败。"""


class NetpbmImageError(RasterImageError):
    """兼容旧调用名。"""


def load_raster_bgr(path: str | Path) -> np.ndarray:
    try:
        image = load_raster_image(path)
    except ImageCodecError as exc:
        raise RasterImageError(str(exc)) from exc

    array = np.frombuffer(image.pixels, dtype=np.uint8)
    if image.channels == 1:
        gray = array.reshape((image.height, image.width))
        return np.repeat(gray[:, :, None], 3, axis=2).copy()
    if image.channels == 3:
        rgb = array.reshape((image.height, image.width, 3))
        return rgb[:, :, ::-1].copy()
    raise RasterImageError(f"不支持的图像通道数: {path} channels={image.channels}")


def load_netpbm_bgr(path: str | Path) -> np.ndarray:
    return load_raster_bgr(path)
