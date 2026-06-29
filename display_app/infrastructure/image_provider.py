from __future__ import annotations

import threading
from typing import Dict

import numpy as np
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider


class CameraImageProvider(QQuickImageProvider):
    """Expose BGR numpy images to QML through image://camera/<id>."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.ImageType.Image)
        self._frames: Dict[str, np.ndarray] = {}
        self._overlays: Dict[str, np.ndarray] = {}
        self._lock = threading.Lock()

    def update_frame(self, camera_id: str, frame: np.ndarray) -> None:
        with self._lock:
            self._frames[camera_id] = frame.copy()

    def update_overlay(self, camera_id: str, overlay: np.ndarray) -> None:
        with self._lock:
            self._overlays[camera_id] = overlay.copy()

    def clear_camera(self, camera_id: str) -> None:
        with self._lock:
            self._frames.pop(camera_id, None)
            self._overlays.pop(camera_id, None)

    def clear_overlay(self, camera_id: str) -> None:
        with self._lock:
            self._overlays.pop(camera_id, None)

    def requestImage(self, image_id: str, size, requested_size):  # noqa: N802
        image_id = image_id.split("?", 1)[0]
        base_id = image_id
        suffix = ""
        for candidate in ("_overlay", "_heatmap", "_original"):
            if image_id.endswith(candidate):
                base_id = image_id[: -len(candidate)]
                suffix = candidate
                break

        with self._lock:
            frame = self._frames.get(base_id)
            overlay = self._overlays.get(base_id)

        if suffix in {"_overlay", "_heatmap"} and overlay is not None:
            return self._bgr_to_qimage(overlay)
        if frame is None and overlay is not None:
            return self._bgr_to_qimage(overlay)
        if frame is None:
            empty = QImage(1, 1, QImage.Format.Format_RGB32)
            empty.fill(0)
            return empty
        return self._bgr_to_qimage(frame)

    def _bgr_to_qimage(self, frame: np.ndarray) -> QImage:
        if frame.ndim == 2:
            rgb = np.repeat(frame[:, :, None], 3, axis=2).copy()
        else:
            rgb = frame[:, :, ::-1].copy()
        height, width = rgb.shape[:2]
        qimage = QImage(rgb.data, width, height, width * 3, QImage.Format.Format_RGB888)
        qimage.rgb_data_holder = rgb
        return qimage
