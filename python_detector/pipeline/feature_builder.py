from __future__ import annotations

from dataclasses import dataclass, field

from python_detector.config.recipe_schema import ModelConfig, Recipe
from python_detector.ipc.data_types import LightFrame
from python_detector.pipeline.reflectance_cube import ReflectanceCube


@dataclass
class FeatureGroup:
    sequence_id: int
    camera_id: str
    roi_name: str
    model_key: str
    features: dict[str, list[int]]
    pose_id: str = ""
    roi_bbox_xyxy_pixel: tuple[int, int, int, int] = (0, 0, 0, 0)
    feature_shape_hw: tuple[int, int] = (0, 0)
    tensor_nchw: list[list[list[list[float]]]] | None = None
    tensor_channel_names: tuple[str, ...] = ()
    evidence_lights_by_channel: dict[str, tuple[str, ...]] = field(default_factory=dict)
    roi_to_source_matrix: tuple[float, ...] | None = None
    source_to_roi_matrix: tuple[float, ...] | None = None
    embedding_summary: dict[str, object] | None = None
    pca_summary: dict[str, object] | None = None
    anomaly_summary: dict[str, object] | None = None


@dataclass(frozen=True)
class FeatureChannelSpec:
    operation: str
    light_ids: tuple[str, ...]


_CHANNEL_ALIASES: dict[str, FeatureChannelSpec] = {
    "ch0_diffuse": FeatureChannelSpec("light", ("DIFFUSE",)),
    "ch1_polar_diffuse": FeatureChannelSpec("light", ("POLAR_DIFFUSE",)),
    "ch2_high_left": FeatureChannelSpec("light", ("HIGH_LEFT",)),
    "ch3_high_right": FeatureChannelSpec("light", ("HIGH_RIGHT",)),
    "ch4_high_max_min": FeatureChannelSpec("max_min", ("HIGH_LEFT", "HIGH_RIGHT")),
    "optional_dark_low_lr_diff": FeatureChannelSpec("abs_diff", ("LOW_LEFT", "LOW_RIGHT")),
    "optional_dark_low_max_min": FeatureChannelSpec("max_min", ("LOW_LEFT", "LOW_RIGHT")),
    "aux_local_contrast": FeatureChannelSpec("local_contrast", ("DIFFUSE",)),
    "aux_specular_removed": FeatureChannelSpec("abs_diff", ("DIFFUSE", "POLAR_DIFFUSE")),
}


class FeatureBuilder:
    def build(self, reflectance_cubes: list[ReflectanceCube], recipe: Recipe) -> list[FeatureGroup]:
        feature_groups: list[FeatureGroup] = []
        for cube in reflectance_cubes:
            primary_model_key = recipe.model_key_for(cube.camera_id, cube.roi_name, cube.pose_id)
            model_keys = (primary_model_key, *recipe.safety_net_model_keys_for(cube.camera_id, cube.roi_name, cube.pose_id))
            features = self._build_feature_dict(cube, self._required_channels(recipe, model_keys))
            feature_groups.append(self._make_feature_group(cube, primary_model_key, recipe.models[primary_model_key], features))
            for model_key in model_keys[1:]:
                feature_groups.append(self._make_feature_group(cube, model_key, recipe.models[model_key], features))
        return feature_groups

    def _required_channels(self, recipe: Recipe, model_keys: tuple[str, ...]) -> tuple[str, ...]:
        channels: list[str] = []
        for model_key in model_keys:
            for channel_name in recipe.models[model_key].input_channels:
                if channel_name not in channels:
                    channels.append(channel_name)
        return tuple(channels)

    def _build_feature_dict(self, cube: ReflectanceCube, channel_names: tuple[str, ...]) -> dict[str, list[int]]:
        return {channel_name: self._build_channel(cube, channel_name) for channel_name in channel_names}

    def _build_channel(self, cube: ReflectanceCube, channel_name: str) -> list[int]:
        spec = self._parse_channel_spec(channel_name)
        if spec.operation == "light":
            return self._required(cube.get(spec.light_ids[0]), channel_name, spec.light_ids[0])
        if spec.operation == "abs_diff":
            return self._abs_diff(
                self._required_frame(cube.get(spec.light_ids[0]), channel_name, spec.light_ids[0]),
                self._required_frame(cube.get(spec.light_ids[1]), channel_name, spec.light_ids[1]),
            )
        if spec.operation == "max_min":
            return self._max_min(
                [
                    self._required_frame(cube.get(light_id), channel_name, light_id)
                    for light_id in spec.light_ids
                ]
            )
        if spec.operation == "local_contrast":
            return self._local_contrast(self._required_frame(cube.get(spec.light_ids[0]), channel_name, spec.light_ids[0]))
        raise ValueError(f"unsupported model input channel: {channel_name}")

    def _make_feature_group(
        self,
        cube: ReflectanceCube,
        model_key: str,
        model_config: ModelConfig,
        features: dict[str, list[int]],
    ) -> FeatureGroup:
        first_frame = next(iter(cube.frames.values()), None)
        feature_shape = (first_frame.height, first_frame.width) if first_frame is not None else (0, 0)
        self._assert_feature_shapes(features, feature_shape, cube.camera_id, cube.roi_name)
        tensor = self._build_tensor(features, model_config.input_channels, feature_shape, model_config.input_scale)
        return FeatureGroup(
            sequence_id=cube.sequence_id,
            camera_id=cube.camera_id,
            pose_id=cube.pose_id,
            roi_name=cube.roi_name,
            model_key=model_key,
            features=features,
            roi_bbox_xyxy_pixel=cube.roi_bbox_xyxy_pixel,
            feature_shape_hw=feature_shape,
            tensor_nchw=tensor,
            tensor_channel_names=model_config.input_channels,
            evidence_lights_by_channel=self._evidence_lights_by_channel(tuple(features)),
            roi_to_source_matrix=cube.roi_to_source_matrix,
            source_to_roi_matrix=cube.source_to_roi_matrix,
        )

    def _required(self, image: LightFrame | None, name: str, light_id: str) -> list[int]:
        return self._sample(self._required_frame(image, name, light_id))

    def _required_frame(self, image: LightFrame | None, name: str, light_id: str) -> LightFrame:
        if image is None:
            raise ValueError(f"required feature source missing: {name} needs {light_id}")
        return image

    def _optional(self, image: LightFrame | None) -> list[int]:
        if image is None:
            return [0] * 64
        return self._sample(image)

    def _abs_diff(self, image_a: LightFrame | None, image_b: LightFrame | None) -> list[int]:
        if image_a is None or image_b is None:
            return [0] * 64
        a = self._sample(image_a)
        b = self._sample(image_b)
        self._assert_same_length("abs_diff", (len(a), len(b)))
        return [abs(x - y) for x, y in zip(a, b)]

    def _max_min(self, images: list[LightFrame | None]) -> list[int]:
        samples = [self._sample(image) for image in images if image is not None]
        if not samples:
            return [0] * 64
        self._assert_same_length("max_min", tuple(len(sample) for sample in samples))
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
            return []
        pixels: list[int] = []
        for row in range(image.height):
            start = row * image.stride_bytes
            end = start + image.width
            pixels.extend(int(value) for value in data[start:end])
        return pixels

    def _build_tensor(
        self,
        features: dict[str, list[int]],
        channel_names: tuple[str, ...],
        shape_hw: tuple[int, int],
        input_scale: float,
    ) -> list[list[list[list[float]]]]:
        height, width = shape_hw
        if height <= 0 or width <= 0:
            raise ValueError("feature tensor shape is invalid")
        channels: list[list[list[float]]] = []
        for channel_name in channel_names:
            values = features.get(channel_name)
            if values is None:
                raise ValueError(f"model input channel missing: {channel_name}")
            channels.append(self._normalize_feature(values, height, width, input_scale))
        return [channels]

    def _normalize_feature(self, values: list[int], height: int, width: int, input_scale: float) -> list[list[float]]:
        if not values:
            raise ValueError("feature channel is empty")
        total = height * width
        if len(values) != total:
            raise ValueError(f"feature channel length mismatch: {len(values)} != {total}")
        return [
            [max(0.0, min(float(values[row * width + col]) / input_scale, 1.0)) for col in range(width)]
            for row in range(height)
        ]

    def _assert_feature_shapes(
        self,
        features: dict[str, list[int]],
        shape_hw: tuple[int, int],
        camera_id: str,
        roi_name: str,
    ) -> None:
        height, width = shape_hw
        expected = height * width
        if expected <= 0:
            raise ValueError(f"{camera_id}/{roi_name}: feature shape is invalid: {shape_hw}")
        mismatched = {
            name: len(values)
            for name, values in features.items()
            if len(values) != expected
        }
        if mismatched:
            raise ValueError(f"{camera_id}/{roi_name}: feature channel length mismatch: {mismatched}, expected={expected}")

    def _assert_same_length(self, operation: str, lengths: tuple[int, ...]) -> None:
        if not lengths:
            return
        if len(set(lengths)) != 1:
            raise ValueError(f"{operation} feature source length mismatch: {lengths}")

    def _evidence_lights_by_channel(self, channel_names: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
        return {
            channel_name: self._parse_channel_spec(channel_name).light_ids
            for channel_name in channel_names
        }

    def _parse_channel_spec(self, channel_name: str) -> FeatureChannelSpec:
        alias = _CHANNEL_ALIASES.get(channel_name)
        if alias is not None:
            return alias
        parts = tuple(part.strip() for part in channel_name.split(":"))
        if len(parts) < 2 or not all(parts):
            raise ValueError(f"unsupported model input channel: {channel_name}")
        operation = parts[0]
        light_ids = parts[1:]
        if operation == "light" and len(light_ids) == 1:
            return FeatureChannelSpec(operation, light_ids)
        if operation == "abs_diff" and len(light_ids) == 2:
            return FeatureChannelSpec(operation, light_ids)
        if operation == "max_min" and len(light_ids) >= 2:
            return FeatureChannelSpec(operation, light_ids)
        if operation == "local_contrast" and len(light_ids) == 1:
            return FeatureChannelSpec(operation, light_ids)
        raise ValueError(f"unsupported model input channel: {channel_name}")
