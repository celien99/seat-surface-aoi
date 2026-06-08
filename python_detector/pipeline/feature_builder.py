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
    roi_bbox_xyxy_pixel: tuple[int, int, int, int] = (0, 0, 0, 0)
    feature_shape_hw: tuple[int, int] = (0, 0)


class FeatureBuilder:
    def build(self, reflectance_cubes: list[ReflectanceCube], recipe: Recipe) -> list[FeatureGroup]:
        feature_groups: list[FeatureGroup] = []
        for cube in reflectance_cubes:
            features = self._build_feature_dict(cube)
            feature_groups.append(self._make_feature_group(cube, recipe.model_key_for(cube.camera_id, cube.roi_name), features))
            for model_key in recipe.safety_net_model_keys_for(cube.camera_id, cube.roi_name):
                feature_groups.append(self._make_feature_group(cube, model_key, features))
        return feature_groups

    def _build_feature_dict(self, cube: ReflectanceCube) -> dict[str, list[int]]:
        diffuse = cube.get("DIFFUSE")
        polar = cube.get("POLAR_DIFFUSE")
        high_left = cube.get("HIGH_LEFT")
        high_right = cube.get("HIGH_RIGHT")
        low_left = cube.get("LOW_LEFT")
        low_right = cube.get("LOW_RIGHT")

        features = {
            "ch0_diffuse": self._required(diffuse, "ch0_diffuse"),
            "ch1_polar_diffuse": self._required(polar, "ch1_polar_diffuse"),
            "ch2_high_left": self._required(high_left, "ch2_high_left"),
            "ch3_high_right": self._required(high_right, "ch3_high_right"),
            "ch4_high_max_min": self._max_min([high_left, high_right]),
        }
        if low_left is not None and low_right is not None:
            features["optional_dark_low_lr_diff"] = self._abs_diff(low_left, low_right)
            features["optional_dark_low_max_min"] = self._max_min([low_left, low_right])
        if diffuse is not None:
            features["aux_local_contrast"] = self._local_contrast(diffuse)
        if diffuse is not None and polar is not None:
            features["aux_specular_removed"] = self._abs_diff(diffuse, polar)
        return features

    def _make_feature_group(self, cube: ReflectanceCube, model_key: str, features: dict[str, list[int]]) -> FeatureGroup:
        first_frame = next(iter(cube.frames.values()), None)
        feature_shape = (first_frame.height, first_frame.width) if first_frame is not None else (0, 0)
        return FeatureGroup(
            sequence_id=cube.sequence_id,
            camera_id=cube.camera_id,
            roi_name=cube.roi_name,
            model_key=model_key,
            features=features,
            roi_bbox_xyxy_pixel=cube.roi_bbox_xyxy_pixel,
            feature_shape_hw=feature_shape,
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
