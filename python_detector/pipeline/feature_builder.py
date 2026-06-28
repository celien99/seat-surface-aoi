from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from python_detector.config.recipe_schema import ModelConfig, Recipe
from python_detector.ipc.data_types import LightFrame
from python_detector.pipeline.reflectance_cube import ReflectanceCube

FeatureValues = list[int] | np.ndarray
TensorNchw = list[list[list[list[float]]]] | np.ndarray



@dataclass
class FeatureGroup:
    sequence_id: int
    camera_id: str
    roi_name: str
    model_key: str
    features: dict[str, FeatureValues]
    pose_id: str = ""
    roi_bbox_xyxy_pixel: tuple[int, int, int, int] = (0, 0, 0, 0)
    feature_shape_hw: tuple[int, int] = (0, 0)
    tensor_nchw: TensorNchw | None = None
    tensor_channel_names: tuple[str, ...] = ()
    evidence_lights_by_channel: dict[str, tuple[str, ...]] = field(default_factory=dict)
    roi_to_source_matrix: tuple[float, ...] | None = None
    source_to_roi_matrix: tuple[float, ...] | None = None
    embedding_summary: dict[str, object] | None = None
    pca_summary: dict[str, object] | None = None
    anomaly_summary: dict[str, object] | None = None

    def evidence_lights(self) -> list[str]:
        """返回与本 feature group 关联的所有 evidence 光源 ID（去重保持顺序）。"""
        evidence: list[str] = []
        for channel_name in self.tensor_channel_names:
            evidence.extend(self.evidence_lights_by_channel.get(channel_name, ()))
        return list(dict.fromkeys(evidence))

    def tensor_shape_nchw(self) -> tuple[int, int, int, int] | None:
        """返回 tensor 的 (N, C, H, W) 形状，tensor 为空时返回 None。"""
        tensor = self.tensor_nchw
        if tensor is None:
            return None
        try:
            import numpy as np
            if isinstance(tensor, np.ndarray):
                shape = tensor.shape
                if len(shape) != 4:
                    return None
                return (int(shape[0]), int(shape[1]), int(shape[2]), int(shape[3]))
        except ImportError:
            pass
        batch = len(tensor)
        channels = len(tensor[0]) if batch > 0 else 0
        height = len(tensor[0][0]) if channels > 0 else 0
        width = len(tensor[0][0][0]) if height > 0 else 0
        return (batch, channels, height, width)


@dataclass(frozen=True)
class FeatureChannelSpec:
    operation: str
    light_ids: tuple[str, ...]


# 通道别名映射（向后兼容旧配方语法）。
# 新配方请直接使用 input_channels 的规范语法:
#   - light:<ID>          直接取光源像素值
#   - abs_diff:<A>:<B>    两光源差的绝对值
#   - max_min:<A>:<B>:... 多光源最大减最小差
#   - local_contrast:<A>  像素减去局部均值
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

    def _build_feature_dict(self, cube: ReflectanceCube, channel_names: tuple[str, ...]) -> dict[str, FeatureValues]:
        return {channel_name: self._build_channel(cube, channel_name) for channel_name in channel_names}

    def _build_channel(self, cube: ReflectanceCube, channel_name: str) -> FeatureValues:
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
        features: dict[str, FeatureValues],
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

    def _required(self, image: LightFrame | None, name: str, light_id: str) -> FeatureValues:
        return self._sample(self._required_frame(image, name, light_id))

    def _required_frame(self, image: LightFrame | None, name: str, light_id: str) -> LightFrame:
        if image is None:
            raise ValueError(f"required feature source missing: {name} needs {light_id}")
        return image

    def _optional(self, image: LightFrame | None) -> FeatureValues:
        if image is None:
            return np.zeros(64, dtype=np.uint8)
        return self._sample(image)

    def _abs_diff(self, image_a: LightFrame | None, image_b: LightFrame | None) -> FeatureValues:
        if image_a is None or image_b is None:
            return np.zeros(64, dtype=np.uint8)
        a = self._sample(image_a)
        b = self._sample(image_b)
        self._assert_same_length("abs_diff", (a.size, b.size))
        return np.abs(a.astype(np.int16, copy=False) - b.astype(np.int16, copy=False)).astype(np.uint8, copy=False)

    def _max_min(self, images: list[LightFrame | None]) -> FeatureValues:
        samples = [self._sample(image) for image in images if image is not None]
        if not samples:
            return np.zeros(64, dtype=np.uint8)
        self._assert_same_length("max_min", tuple(sample.size for sample in samples))
        stacked = np.stack(samples).astype(np.int16, copy=False)
        return (stacked.max(axis=0) - stacked.min(axis=0)).astype(np.uint8, copy=False)

    def _local_contrast(self, image: LightFrame | None) -> FeatureValues:
        if image is None:
            return np.zeros(64, dtype=np.uint8)
        sample = self._sample(image)
        mean = int(sample.astype(np.uint64, copy=False).sum() // max(sample.size, 1))
        return np.abs(sample.astype(np.int16, copy=False) - mean).astype(np.uint8, copy=False)

    def _sample(self, image: LightFrame) -> np.ndarray:
        expected = image.stride_bytes * image.height
        if expected <= 0:
            return np.asarray([], dtype=np.uint8)
        raw = np.frombuffer(image.image, dtype=np.uint8, count=expected)
        if image.stride_bytes == image.width:
            return raw[: image.width * image.height].reshape(-1)
        return raw.reshape(image.height, image.stride_bytes)[:, : image.width].reshape(-1)

    def _build_tensor(
        self,
        features: dict[str, FeatureValues],
        channel_names: tuple[str, ...],
        shape_hw: tuple[int, int],
        input_scale: float,
    ) -> np.ndarray:
        height, width = shape_hw
        if height <= 0 or width <= 0:
            raise ValueError("feature tensor shape is invalid")
        channels: list[np.ndarray] = []
        if not channel_names:
            return np.empty((1, 0, height, width), dtype=np.float32)
        for channel_name in channel_names:
            values = features.get(channel_name)
            if values is None:
                raise ValueError(f"model input channel missing: {channel_name}")
            channels.append(self._normalize_feature(values, height, width, input_scale))
        return np.stack(channels, axis=0)[None, :, :, :]

    def _normalize_feature(self, values: FeatureValues, height: int, width: int, input_scale: float) -> np.ndarray:
        array = np.asarray(values)
        if array.size == 0:
            raise ValueError("feature channel is empty")
        total = height * width
        if array.size != total:
            raise ValueError(f"feature channel length mismatch: {array.size} != {total}")
        normalized = array.reshape(height, width).astype(np.float32, copy=False) / np.float32(input_scale)
        return np.clip(normalized, 0.0, 1.0)

    def _assert_feature_shapes(
        self,
        features: dict[str, FeatureValues],
        shape_hw: tuple[int, int],
        camera_id: str,
        roi_name: str,
    ) -> None:
        height, width = shape_hw
        expected = height * width
        if expected <= 0:
            raise ValueError(f"{camera_id}/{roi_name}: feature shape is invalid: {shape_hw}")
        mismatched = {
            name: np.asarray(values).size
            for name, values in features.items()
            if np.asarray(values).size != expected
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
