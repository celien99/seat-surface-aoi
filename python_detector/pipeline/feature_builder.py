from __future__ import annotations

from dataclasses import dataclass

from python_detector.config.recipe_schema import Recipe
from python_detector.ipc.data_types import LightFrame
from python_detector.pipeline.reflectance_cube import ReflectanceCube


@dataclass
class FeatureGroup:
    sequence_id: int
    camera_id: str
    roi_name: str
    model_key: str
    features: dict[str, list[int]]


class FeatureBuilder:
    def build(self, reflectance_cubes: list[ReflectanceCube], recipe: Recipe) -> list[FeatureGroup]:
        return [self._build_roi_features(cube, recipe) for cube in reflectance_cubes]

    def _build_roi_features(self, cube: ReflectanceCube, recipe: Recipe) -> FeatureGroup:
        diffuse = cube.get("DIFFUSE")
        polar = cube.get("POLAR_DIFFUSE")
        high_left = cube.get("HIGH_LEFT")
        high_right = cube.get("HIGH_RIGHT")
        low_left = cube.get("LOW_LEFT")
        low_right = cube.get("LOW_RIGHT")

        features = {
            "raw_diffuse": self._required(diffuse, "raw_diffuse"),
            "raw_polar": self._optional(polar),
            "high_lr_diff": self._abs_diff(high_left, high_right),
            "high_max_min": self._max_min([high_left, high_right]),
            "low_lr_diff": self._abs_diff(low_left, low_right),
            "low_max_min": self._max_min([low_left, low_right]),
            "local_contrast": self._local_contrast(diffuse),
            "specular_removed": self._abs_diff(diffuse, polar),
        }
        return FeatureGroup(
            sequence_id=cube.sequence_id,
            camera_id=cube.camera_id,
            roi_name=cube.roi_name,
            model_key=f"{recipe.recipe_id}:{cube.camera_id}:{cube.roi_name}",
            features=features,
        )

    def _required(self, image: LightFrame | None, name: str) -> list[int]:
        if image is None:
            raise ValueError(f"required feature source missing: {name}")
        return self._sample(image)

    def _optional(self, image: LightFrame | None) -> list[int]:
        if image is None:
            return [0] * 64
        return self._sample(image)

    def _abs_diff(self, image_a: LightFrame | None, image_b: LightFrame | None) -> list[int]:
        if image_a is None or image_b is None:
            return [0] * 64
        a = self._sample(image_a)
        b = self._sample(image_b)
        return [abs(x - y) for x, y in zip(a, b)]

    def _max_min(self, images: list[LightFrame | None]) -> list[int]:
        samples = [self._sample(image) for image in images if image is not None]
        if not samples:
            return [0] * 64
        return [max(values) - min(values) for values in zip(*samples)]

    def _local_contrast(self, image: LightFrame | None) -> list[int]:
        if image is None:
            return [0] * 64
        sample = self._sample(image)
        mean = sum(sample) // max(len(sample), 1)
        return [abs(value - mean) for value in sample]

    def _sample(self, image: LightFrame) -> list[int]:
        expected = image.stride_bytes * image.height
        data = image.image[:expected]
        if len(data) == 0:
            return [0] * 64
        step = max(len(data) // 64, 1)
        sampled = [int(data[i]) for i in range(0, len(data), step)][:64]
        if len(sampled) < 64:
            sampled.extend([0] * (64 - len(sampled)))
        return sampled

