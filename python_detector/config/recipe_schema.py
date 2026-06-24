from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from python_detector.paths import DEFAULT_CONFIG_DIR


class RecipeValidationError(ValueError):
    """配方校验失败。"""


@dataclass(frozen=True)
class V4LightConfig:
    semantic_to_light_id: dict[str, str] = field(
        default_factory=lambda: {
            "DOME": "DIFFUSE",
            "DARKFIELD_L": "HIGH_LEFT",
            "BRIGHTFIELD": "POLAR_DIFFUSE",
        }
    )


@dataclass(frozen=True)
class RoiLocatorConfig:
    backend: str = "template"
    dome_semantic_light: str = "DOME"
    model_path: str | None = None
    min_confidence: float = 0.5
    max_pose_error_px: float = 4.0
    mask_threshold: float = 0.5
    min_mask_area_px: int = 1
    max_mask_area_ratio: float = 1.0
    input_width: int = 0
    input_height: int = 0
    input_channels: int = 1
    output_decode: str = "yolo_xyxy_rows"
    bbox_format: str = "xyxy_pixel"
    class_names: tuple[str, ...] = ("seat",)
    fail_policy: str = "RECHECK"


@dataclass(frozen=True)
class QualityConfig:
    required_lights: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    max_saturation_ratio: float = 0.01
    min_mean_gray: float = 20.0
    max_mean_gray: float = 235.0
    min_sharpness: float = 1.0
    min_motion_gradient: float = 1.0
    max_light_mean_delta: float = 80.0
    max_registration_error_px: float = 1.5
    max_capture_span_us: int = 500_000
    max_exposure_delta_us: int = 200
    max_gain_delta: float = 0.2
    require_monotonic_timestamps: bool = True
    require_unique_frame_indices: bool = True


@dataclass(frozen=True)
class RegistrationConfig:
    base_light_id: str = "POLAR_DIFFUSE"
    base_light_fallback: str = "DIFFUSE"
    fail_policy: str = "RECHECK"
    method: str = "fixed_calibration"
    max_iterations: int = 30
    convergence_epsilon: float = 1e-4
    search_radius_px: int = 2
    min_correlation: float = 0.05


@dataclass(frozen=True)
class CameraRecipe:
    camera_id: str
    pose_id: str = ""
    enabled: bool = True
    model_key: str = "default"
    safety_net_model_key: str | None = None
    roi_template: str = "config/roi/default_roi.yaml"
    calibration_id: str = "calib/simulated_v1"
    base_light_id: str = "POLAR_DIFFUSE"
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    roi_models: dict[str, str] = field(default_factory=dict)
    roi_safety_net_models: dict[str, str] = field(default_factory=dict)
    pixel_size_mm: float | None = None


@dataclass(frozen=True)
class CameraDefaults:
    model_key: str = "default"
    safety_net_model_key: str | None = None
    roi_template: str = "python_detector/config/roi/default_roi.yaml"
    calibration_id: str = "calib/simulated_v1"
    base_light_id: str = "POLAR_DIFFUSE"
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    roi_models: dict[str, str] = field(default_factory=dict)
    roi_safety_net_models: dict[str, str] = field(default_factory=dict)
    pixel_size_mm: float | None = None


@dataclass(frozen=True)
class ThresholdConfig:
    ng_score: float = 0.35
    recheck_score: float = 0.20
    min_area_px: int = 1


@dataclass(frozen=True)
class FusionConfig:
    iou_threshold: float = 0.5
    class_aware: bool = True
    max_candidates_per_roi: int = 16


@dataclass(frozen=True)
class ModelConfig:
    backend: str = "fake"
    model_path: str | None = None
    fake_mode: str = "auto"
    model_family: str = "supervised"
    role: str = "primary"
    input_channels: tuple[str, ...] = (
        "ch0_diffuse",
        "ch1_polar_diffuse",
        "ch2_high_left",
    )
    input_scale: float = 255.0
    class_names: tuple[str, ...] = ("scratch",)
    output_decode: str = "none"
    bbox_format: str = "xyxy_pixel"
    score_threshold: float = 0.0
    embedding_backend: str = "none"
    embedding_model_path: str | None = None
    embedding_version: str = "none"
    embedding_dim: int = 10
    embedding_layers: tuple[str, ...] = ()
    pca_path: str | None = None
    pca_version: str | None = None
    memory_bank_path: str | None = None
    faiss_index_path: str | None = None
    coreset_ratio: float = 1.0
    knn_k: int = 1
    anomaly_score_scale: float = 1.0


@dataclass(frozen=True)
class TraceConfig:
    enabled: bool = True
    root_dir: str = "trace"
    save_ok_ratio: float = 0.0
    save_ng: bool = True
    save_recheck: bool = True


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    sku: str
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    v4_lights: V4LightConfig = field(default_factory=V4LightConfig)
    camera_defaults: CameraDefaults = field(default_factory=CameraDefaults)
    cameras: tuple[CameraRecipe, ...] = field(default_factory=tuple)
    quality: QualityConfig = field(default_factory=QualityConfig)
    roi_locator: RoiLocatorConfig = field(default_factory=RoiLocatorConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    thresholds: dict[str, ThresholdConfig] = field(default_factory=dict)
    models: dict[str, ModelConfig] = field(default_factory=lambda: {"default": ModelConfig()})
    trace: TraceConfig = field(default_factory=TraceConfig)

    def camera(self, camera_id: str, pose_id: str | None = None) -> CameraRecipe | None:
        exact = self.exact_camera(camera_id, pose_id)
        if exact is not None:
            return exact
        pose_label = pose_id or camera_id
        if pose_label == camera_id or self.default_camera_accepts_dynamic_pose(camera_id):
            return self.default_camera(camera_id)
        return None

    def exact_camera(self, camera_id: str, pose_id: str | None = None) -> CameraRecipe | None:
        pose_label = pose_id or camera_id
        for camera in self.cameras:
            if camera.camera_id == camera_id and (camera.pose_id or camera.camera_id) == pose_label:
                return camera
        return None

    def default_camera(self, camera_id: str) -> CameraRecipe | None:
        for camera in self.cameras:
            if camera.camera_id == camera_id and camera.pose_id in ("", camera_id):
                return camera
        return None

    def accepts_camera_pose(self, camera_id: str, pose_id: str | None = None) -> bool:
        if self.exact_camera(camera_id, pose_id) is not None:
            return True
        return self.default_camera_accepts_dynamic_pose(camera_id)

    def configured_view_keys(self) -> set[tuple[str, str]]:
        return {(camera.camera_id, camera.pose_id or camera.camera_id) for camera in self.cameras}

    def required_view_keys(self) -> set[tuple[str, str]]:
        return self.configured_view_keys()

    def pose_uses_default_camera(self, camera_id: str, pose_id: str | None = None) -> bool:
        pose_label = pose_id or camera_id
        return (
            pose_label != camera_id
            and self.exact_camera(camera_id, pose_label) is None
            and self.default_camera_accepts_dynamic_pose(camera_id)
        )

    def default_camera_accepts_dynamic_pose(self, camera_id: str) -> bool:
        return self.default_camera(camera_id) is not None and not self.has_explicit_camera_poses(camera_id)

    def has_explicit_camera_poses(self, camera_id: str) -> bool:
        return any(
            camera.camera_id == camera_id and camera.pose_id not in ("", camera.camera_id)
            for camera in self.cameras
        )

    def camera_label(self, camera_id: str, pose_id: str | None = None) -> str:
        pose_label = pose_id or camera_id
        return camera_id if pose_label == camera_id else f"{camera_id}/{pose_label}"

    def missing_view_message(self, camera_id: str, pose_id: str) -> str:
        if pose_id == camera_id:
            return f"{camera_id}: missing configured camera bundle"
        return f"{camera_id}/{pose_id}: missing configured camera pose bundle"

    def view_not_enabled_message(self, camera_id: str, pose_id: str | None = None) -> str:
        return f"{self.camera_label(camera_id, pose_id)}: camera pose not enabled by recipe"

    def model_key_for(self, camera_id: str, roi_name: str, pose_id: str | None = None) -> str:
        camera = self.camera(camera_id, pose_id)
        if camera is None:
            return "default"
        return camera.roi_models.get(roi_name, camera.model_key)

    def safety_net_model_keys_for(self, camera_id: str, roi_name: str, pose_id: str | None = None) -> tuple[str, ...]:
        camera = self.camera(camera_id, pose_id)
        if camera is None:
            return ()
        model_key = camera.roi_safety_net_models.get(roi_name, camera.safety_net_model_key)
        return () if model_key is None else (model_key,)

    def semantic_light_id(self, semantic_light: str) -> str:
        return self.v4_lights.semantic_to_light_id.get(semantic_light, semantic_light)


class RecipeManager:
    def __init__(self, recipe_dir: str | Path | None = None) -> None:
        self.recipe_dir = Path(recipe_dir) if recipe_dir is not None else DEFAULT_CONFIG_DIR
        self._recipes: dict[str, Recipe] = {}
        self._load_recipe_dir()

    def load(self, recipe_id: str) -> Recipe:
        if recipe_id in self._recipes:
            return self._recipes[recipe_id]
        raise RecipeValidationError(f"配方不存在: {recipe_id}")

    def _load_recipe_dir(self) -> None:
        if not self.recipe_dir.exists():
            raise RecipeValidationError(f"配方目录不存在: {self.recipe_dir}")
        for path in sorted(self.recipe_dir.glob("*.yaml")):
            if path.name.endswith(".example.yaml"):
                continue
            recipe = load_recipe_file(path)
            self._recipes[recipe.recipe_id] = recipe


def load_recipe_file(path: str | Path) -> Recipe:
    path = Path(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RecipeValidationError(f"配方文件必须是字典: {path}")
    return recipe_from_dict(data)


def recipe_from_dict(data: dict[str, Any]) -> Recipe:
    recipe_id = _required_str(data, "recipe_id")
    sku = _required_str(data, "sku")
    light_order = _str_tuple(data.get("light_order", ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")), "light_order")
    v4_lights = _v4_lights_from_dict(_dict(data.get("v4_lights", {}), "v4_lights"))
    quality = _quality_from_dict(_dict(data.get("quality", {}), "quality"))
    roi_locator = _roi_locator_from_dict(_dict(data.get("roi_locator", {}), "roi_locator"))
    registration = _registration_from_dict(_dict(data.get("registration", {}), "registration"))
    fusion = _fusion_from_dict(_dict(data.get("fusion", {}), "fusion"))
    camera_defaults = _camera_defaults_from_dict(
        _dict(data.get("camera_defaults", {}), "camera_defaults"),
        light_order,
        registration.base_light_id,
    )
    cameras = _cameras_from_dict(data.get("cameras", {}), camera_defaults)
    thresholds = _thresholds_from_dict(_dict(data.get("thresholds", {}), "thresholds"))
    models = _models_from_dict(_dict(data.get("models", {"default": {"backend": "fake"}}), "models"))
    trace = _trace_from_dict(_dict(data.get("trace", {}), "trace"))

    _validate_lights(light_order, quality.required_lights)
    _validate_v4_lights(light_order, v4_lights)
    _validate_roi_locator_light(v4_lights, roi_locator, light_order)
    _validate_registration_lights(light_order, quality.required_lights, registration)
    _validate_camera_lights(cameras, quality.required_lights, registration)
    _validate_model_refs(cameras, models)
    _validate_model_thresholds(models, thresholds)
    if not cameras:
        raise RecipeValidationError("至少需要配置一个启用机位")
    return Recipe(
        recipe_id=recipe_id,
        sku=sku,
        light_order=light_order,
        v4_lights=v4_lights,
        camera_defaults=camera_defaults,
        cameras=cameras,
        quality=quality,
        roi_locator=roi_locator,
        registration=registration,
        fusion=fusion,
        thresholds=thresholds,
        models=models,
        trace=trace,
    )


def _quality_from_dict(data: dict[str, Any]) -> QualityConfig:
    min_mean_gray = _gray_value(data.get("min_mean_gray", 20.0), "quality.min_mean_gray")
    max_mean_gray = _gray_value(data.get("max_mean_gray", 235.0), "quality.max_mean_gray")
    if min_mean_gray > max_mean_gray:
        raise RecipeValidationError(
            f"quality.min_mean_gray 不能大于 max_mean_gray: {min_mean_gray} > {max_mean_gray}"
        )
    return QualityConfig(
        required_lights=_str_tuple(data.get("required_lights", ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")), "quality.required_lights"),
        max_saturation_ratio=_ratio(data.get("max_saturation_ratio", 0.01), "quality.max_saturation_ratio"),
        min_mean_gray=min_mean_gray,
        max_mean_gray=max_mean_gray,
        min_sharpness=_non_negative_float(data.get("min_sharpness", 1.0), "quality.min_sharpness"),
        min_motion_gradient=_non_negative_float(data.get("min_motion_gradient", 1.0), "quality.min_motion_gradient"),
        max_light_mean_delta=_non_negative_float(data.get("max_light_mean_delta", 80.0), "quality.max_light_mean_delta"),
        max_registration_error_px=_non_negative_float(data.get("max_registration_error_px", 1.5), "quality.max_registration_error_px"),
        max_capture_span_us=_non_negative_int(data.get("max_capture_span_us", 500_000), "quality.max_capture_span_us"),
        max_exposure_delta_us=_non_negative_int(data.get("max_exposure_delta_us", 200), "quality.max_exposure_delta_us"),
        max_gain_delta=_non_negative_float(data.get("max_gain_delta", 0.2), "quality.max_gain_delta"),
        require_monotonic_timestamps=bool(data.get("require_monotonic_timestamps", True)),
        require_unique_frame_indices=bool(data.get("require_unique_frame_indices", True)),
    )


def _v4_lights_from_dict(data: dict[str, Any]) -> V4LightConfig:
    raw_mapping = data.get(
        "semantic_to_light_id",
        {
            "DOME": "DIFFUSE",
            "DARKFIELD_L": "HIGH_LEFT",
            "BRIGHTFIELD": "POLAR_DIFFUSE",
        },
    )
    mapping = {
        _str(key, f"v4_lights.semantic_to_light_id.{key}"): _str(
            value,
            f"v4_lights.semantic_to_light_id.{key}",
        )
        for key, value in _dict(raw_mapping, "v4_lights.semantic_to_light_id").items()
    }
    required_semantics = ("DOME", "DARKFIELD_L", "BRIGHTFIELD")
    missing = [semantic for semantic in required_semantics if semantic not in mapping]
    if missing:
        raise RecipeValidationError(f"v4_lights.semantic_to_light_id 缺少 V4 语义光源: {missing}")
    return V4LightConfig(semantic_to_light_id=mapping)


def _roi_locator_from_dict(data: dict[str, Any]) -> RoiLocatorConfig:
    backend = _str(data.get("backend", "template"), "roi_locator.backend")
    if backend not in {"template", "fake_yolo", "onnx_yolo", "onnx_yolo_seg"}:
        raise RecipeValidationError(f"roi_locator.backend 不支持: {backend}")
    output_decode = _str(data.get("output_decode", "yolo_xyxy_rows"), "roi_locator.output_decode")
    if output_decode not in {"yolo_xyxy_rows", "ultralytics_yolo", "segmentation_rows", "ultralytics_yolo_seg"}:
        raise RecipeValidationError(
            "roi_locator.output_decode 必须是 yolo_xyxy_rows、ultralytics_yolo、"
            "segmentation_rows 或 ultralytics_yolo_seg"
        )
    if backend == "onnx_yolo_seg" and output_decode not in {"segmentation_rows", "ultralytics_yolo_seg"}:
        raise RecipeValidationError("roi_locator.backend=onnx_yolo_seg 必须使用 segmentation_rows 或 ultralytics_yolo_seg")
    if backend in {"fake_yolo", "onnx_yolo"} and output_decode not in {"yolo_xyxy_rows", "ultralytics_yolo"}:
        raise RecipeValidationError("roi_locator bbox 后端必须使用 yolo_xyxy_rows 或 ultralytics_yolo")
    input_channels = _positive_int(data.get("input_channels", 1), "roi_locator.input_channels")
    if input_channels not in {1, 3}:
        raise RecipeValidationError("roi_locator.input_channels 必须是 1 或 3")
    bbox_format = _bbox_format(data.get("bbox_format", "xyxy_pixel"), "roi_locator.bbox_format")
    return RoiLocatorConfig(
        backend=backend,
        dome_semantic_light=_str(data.get("dome_semantic_light", "DOME"), "roi_locator.dome_semantic_light"),
        model_path=None
        if data.get("model_path") in (None, "")
        else _str(data.get("model_path"), "roi_locator.model_path"),
        min_confidence=_ratio(data.get("min_confidence", 0.5), "roi_locator.min_confidence"),
        max_pose_error_px=_non_negative_float(data.get("max_pose_error_px", 4.0), "roi_locator.max_pose_error_px"),
        mask_threshold=_ratio(data.get("mask_threshold", 0.5), "roi_locator.mask_threshold"),
        min_mask_area_px=_positive_int(data.get("min_mask_area_px", 1), "roi_locator.min_mask_area_px"),
        max_mask_area_ratio=_ratio(data.get("max_mask_area_ratio", 1.0), "roi_locator.max_mask_area_ratio"),
        input_width=_non_negative_int(data.get("input_width", 0), "roi_locator.input_width"),
        input_height=_non_negative_int(data.get("input_height", 0), "roi_locator.input_height"),
        input_channels=input_channels,
        output_decode=output_decode,
        bbox_format=bbox_format,
        class_names=_unique_str_tuple(data.get("class_names", ("seat",)), "roi_locator.class_names"),
        fail_policy=_decision(data.get("fail_policy", "RECHECK"), "roi_locator.fail_policy"),
    )


def _registration_from_dict(data: dict[str, Any]) -> RegistrationConfig:
    method = _str(data.get("method", "fixed_calibration"), "registration.method")
    if method not in {"fixed_calibration", "ecc"}:
        raise RecipeValidationError("registration.method 必须是 fixed_calibration 或 ecc")
    return RegistrationConfig(
        base_light_id=_str(data.get("base_light_id", "POLAR_DIFFUSE"), "registration.base_light_id"),
        base_light_fallback=_str(data.get("base_light_fallback", "DIFFUSE"), "registration.base_light_fallback"),
        fail_policy=_decision(data.get("fail_policy", "RECHECK"), "registration.fail_policy"),
        method=method,
        max_iterations=_positive_int(data.get("max_iterations", 30), "registration.max_iterations"),
        convergence_epsilon=_positive_float(data.get("convergence_epsilon", 1e-4), "registration.convergence_epsilon"),
        search_radius_px=_non_negative_int(data.get("search_radius_px", 2), "registration.search_radius_px"),
        min_correlation=_ratio(data.get("min_correlation", 0.05), "registration.min_correlation"),
    )


def _fusion_from_dict(data: dict[str, Any]) -> FusionConfig:
    return FusionConfig(
        iou_threshold=_ratio(data.get("iou_threshold", 0.5), "fusion.iou_threshold"),
        class_aware=bool(data.get("class_aware", True)),
        max_candidates_per_roi=_positive_int(data.get("max_candidates_per_roi", 16), "fusion.max_candidates_per_roi"),
    )


def _camera_defaults_from_dict(
    data: dict[str, Any],
    default_light_order: tuple[str, ...],
    default_base_light_id: str,
) -> CameraDefaults:
    light_order = _str_tuple(data.get("light_order", default_light_order), "camera_defaults.light_order")
    _validate_lights(light_order, ())
    return CameraDefaults(
        model_key=_str(data.get("model_key", "default"), "camera_defaults.model_key"),
        safety_net_model_key=_optional_str(
            data.get("safety_net_model_key"),
            "camera_defaults.safety_net_model_key",
        ),
        roi_template=_str(
            data.get("roi_template", "python_detector/config/roi/default_roi.yaml"),
            "camera_defaults.roi_template",
        ),
        calibration_id=_str(data.get("calibration_id", "calib/simulated_v1"), "camera_defaults.calibration_id"),
        base_light_id=_str(data.get("base_light_id", default_base_light_id), "camera_defaults.base_light_id"),
        light_order=light_order,
        roi_models={
            str(k): _str(v, f"camera_defaults.roi_models.{k}")
            for k, v in _dict(data.get("roi_models", {}), "camera_defaults.roi_models").items()
        },
        roi_safety_net_models={
            str(k): _str(v, f"camera_defaults.roi_safety_net_models.{k}")
            for k, v in _dict(data.get("roi_safety_net_models", {}), "camera_defaults.roi_safety_net_models").items()
        },
        pixel_size_mm=_optional_float(data.get("pixel_size_mm"), "camera_defaults.pixel_size_mm"),
    )


def _cameras_from_dict(data: Any, camera_defaults: CameraDefaults) -> tuple[CameraRecipe, ...]:
    if isinstance(data, list):
        items = []
        for index, item in enumerate(data):
            raw = _dict(item, f"cameras[{index}]")
            camera_id = _str(raw.get("camera_id"), f"cameras[{index}].camera_id")
            pose_id = raw.get("pose_id", camera_id)
            pose_label = pose_id if isinstance(pose_id, str) and pose_id else camera_id
            items.append((f"{camera_id}/{pose_label}", raw))
    elif isinstance(data, dict):
        items = [(str(camera_id), raw) for camera_id, raw in data.items()]
    else:
        raise RecipeValidationError("cameras 必须是字典或列表")
    cameras: list[CameraRecipe] = []
    for camera_id, raw in items:
        raw = _dict(raw, f"cameras.{camera_id}")
        enabled = bool(raw.get("enabled", True))
        if not enabled:
            continue
        light_order = _str_tuple(raw.get("light_order", camera_defaults.light_order), f"cameras.{camera_id}.light_order")
        _validate_lights(light_order, ())
        cameras.append(
            CameraRecipe(
                camera_id=_str(raw.get("camera_id", camera_id), f"cameras.{camera_id}.camera_id"),
                pose_id=_str(raw.get("pose_id", raw.get("camera_id", camera_id)), f"cameras.{camera_id}.pose_id"),
                enabled=enabled,
                model_key=_str(raw.get("model_key", camera_defaults.model_key), f"cameras.{camera_id}.model_key"),
                safety_net_model_key=_optional_str(
                    raw.get("safety_net_model_key", camera_defaults.safety_net_model_key),
                    f"cameras.{camera_id}.safety_net_model_key",
                ),
                roi_template=_str(
                    raw.get("roi_template", camera_defaults.roi_template),
                    f"cameras.{camera_id}.roi_template",
                ),
                calibration_id=_str(
                    raw.get("calibration_id", camera_defaults.calibration_id),
                    f"cameras.{camera_id}.calibration_id",
                ),
                base_light_id=_str(
                    raw.get("base_light_id", camera_defaults.base_light_id),
                    f"cameras.{camera_id}.base_light_id",
                ),
                light_order=light_order,
                roi_models={
                    str(k): _str(v, f"cameras.{camera_id}.roi_models.{k}")
                    for k, v in _dict(
                        raw.get("roi_models", camera_defaults.roi_models),
                        f"cameras.{camera_id}.roi_models",
                    ).items()
                },
                roi_safety_net_models={
                    str(k): _str(v, f"cameras.{camera_id}.roi_safety_net_models.{k}")
                    for k, v in _dict(
                        raw.get("roi_safety_net_models", camera_defaults.roi_safety_net_models),
                        f"cameras.{camera_id}.roi_safety_net_models",
                    ).items()
                },
                pixel_size_mm=_optional_float(
                    raw.get("pixel_size_mm", camera_defaults.pixel_size_mm),
                    f"cameras.{camera_id}.pixel_size_mm",
                ),
            )
        )
    return tuple(cameras)


def _thresholds_from_dict(data: dict[str, Any]) -> dict[str, ThresholdConfig]:
    thresholds: dict[str, ThresholdConfig] = {}
    for class_name, raw in data.items():
        raw = _dict(raw, f"thresholds.{class_name}")
        ng_score = _ratio(raw.get("ng_score", 0.35), f"thresholds.{class_name}.ng_score")
        recheck_score = _ratio(raw.get("recheck_score", 0.20), f"thresholds.{class_name}.recheck_score")
        if recheck_score > ng_score:
            raise RecipeValidationError(
                f"thresholds.{class_name}.recheck_score 不能大于 ng_score: {recheck_score} > {ng_score}"
            )
        thresholds[str(class_name)] = ThresholdConfig(
            ng_score=ng_score,
            recheck_score=recheck_score,
            min_area_px=_non_negative_int(raw.get("min_area_px", 1), f"thresholds.{class_name}.min_area_px"),
        )
    return thresholds


def _models_from_dict(data: dict[str, Any]) -> dict[str, ModelConfig]:
    models: dict[str, ModelConfig] = {}
    for model_key, raw in data.items():
        raw = _dict(raw, f"models.{model_key}")
        backend = _str(raw.get("backend", "fake"), f"models.{model_key}.backend")
        if backend not in {"fake", "onnx", "patchcore_knn"}:
            raise RecipeValidationError(f"不支持的模型后端: {backend}")
        model_family = _str(raw.get("model_family", "supervised"), f"models.{model_key}.model_family")
        if model_family not in {"supervised", "patchcore", "efficientad", "yolo_seg", "classifier"}:
            raise RecipeValidationError(f"不支持的模型家族: {model_family}")
        role = _str(raw.get("role", "primary"), f"models.{model_key}.role")
        if role not in {"primary", "safety_net"}:
            raise RecipeValidationError(f"模型角色必须是 primary 或 safety_net: {model_key}")
        if backend == "patchcore_knn" and model_family != "patchcore":
            raise RecipeValidationError(f"models.{model_key}.backend=patchcore_knn 必须配置 model_family=patchcore")
        fake_mode = _str(raw.get("fake_mode", "auto"), f"models.{model_key}.fake_mode")
        if fake_mode not in {"auto", "ok", "ng", "recheck"}:
            raise RecipeValidationError(f"models.{model_key}.fake_mode 必须是 auto、ok、ng 或 recheck")
        input_channels = _unique_str_tuple(
            raw.get(
                "input_channels",
                ("ch0_diffuse", "ch1_polar_diffuse", "ch2_high_left"),
            ),
            f"models.{model_key}.input_channels",
        )
        class_names = _unique_str_tuple(raw.get("class_names", ("scratch",)), f"models.{model_key}.class_names")
        embedding_backend = _str(raw.get("embedding_backend", "none"), f"models.{model_key}.embedding_backend")
        if embedding_backend not in {"none", "statistical", "onnx_wideresnet50"}:
            raise RecipeValidationError(
                f"models.{model_key}.embedding_backend 必须是 none、statistical 或 onnx_wideresnet50"
            )
        coreset_ratio = _ratio(raw.get("coreset_ratio", 1.0), f"models.{model_key}.coreset_ratio")
        if coreset_ratio <= 0.0:
            raise RecipeValidationError(f"models.{model_key}.coreset_ratio 必须大于 0")
        models[str(model_key)] = ModelConfig(
            backend=backend,
            model_path=None if raw.get("model_path") in (None, "") else _str(raw.get("model_path"), f"models.{model_key}.model_path"),
            fake_mode=fake_mode,
            model_family=model_family,
            role=role,
            input_channels=input_channels,
            input_scale=_positive_float(raw.get("input_scale", 255.0), f"models.{model_key}.input_scale"),
            class_names=class_names,
            output_decode=_output_decode(raw.get("output_decode", "none"), f"models.{model_key}.output_decode"),
            bbox_format=_bbox_format(raw.get("bbox_format", "xyxy_pixel"), f"models.{model_key}.bbox_format"),
            score_threshold=_ratio(raw.get("score_threshold", 0.0), f"models.{model_key}.score_threshold"),
            embedding_backend=embedding_backend,
            embedding_model_path=None
            if raw.get("embedding_model_path") in (None, "")
            else _str(raw.get("embedding_model_path"), f"models.{model_key}.embedding_model_path"),
            embedding_version=_str(raw.get("embedding_version", "none"), f"models.{model_key}.embedding_version"),
            embedding_dim=_positive_int(raw.get("embedding_dim", 10), f"models.{model_key}.embedding_dim"),
            embedding_layers=_optional_unique_str_tuple(
                raw.get("embedding_layers", ()),
                f"models.{model_key}.embedding_layers",
            ),
            pca_path=None if raw.get("pca_path") in (None, "") else _str(raw.get("pca_path"), f"models.{model_key}.pca_path"),
            pca_version=None
            if raw.get("pca_version") in (None, "")
            else _str(raw.get("pca_version"), f"models.{model_key}.pca_version"),
            memory_bank_path=None
            if raw.get("memory_bank_path") in (None, "")
            else _str(raw.get("memory_bank_path"), f"models.{model_key}.memory_bank_path"),
            faiss_index_path=None
            if raw.get("faiss_index_path") in (None, "")
            else _str(raw.get("faiss_index_path"), f"models.{model_key}.faiss_index_path"),
            coreset_ratio=coreset_ratio,
            knn_k=_positive_int(raw.get("knn_k", 1), f"models.{model_key}.knn_k"),
            anomaly_score_scale=_positive_float(
                raw.get("anomaly_score_scale", 1.0),
                f"models.{model_key}.anomaly_score_scale",
            ),
        )
    if "default" not in models:
        models["default"] = ModelConfig()
    return models


def _trace_from_dict(data: dict[str, Any]) -> TraceConfig:
    return TraceConfig(
        enabled=bool(data.get("enabled", True)),
        root_dir=_str(data.get("root_dir", "trace"), "trace.root_dir"),
        save_ok_ratio=_float(data.get("save_ok_ratio", 0.0), "trace.save_ok_ratio"),
        save_ng=bool(data.get("save_ng", True)),
        save_recheck=bool(data.get("save_recheck", True)),
    )


def _validate_lights(light_order: tuple[str, ...], required_lights: tuple[str, ...]) -> None:
    if not light_order:
        raise RecipeValidationError("light_order 不能为空")
    duplicated = sorted({light for light in light_order if light_order.count(light) > 1})
    if duplicated:
        raise RecipeValidationError(f"light_order 存在重复光源: {duplicated}")
    missing = [light for light in required_lights if light not in light_order]
    if missing:
        raise RecipeValidationError(f"required_lights 不在 light_order 中: {missing}")


def _validate_v4_lights(light_order: tuple[str, ...], v4_lights: V4LightConfig) -> None:
    for semantic, light_id in v4_lights.semantic_to_light_id.items():
        if light_id not in light_order:
            raise RecipeValidationError(f"v4_lights.semantic_to_light_id.{semantic} 不在 light_order 中: {light_id}")


def _validate_roi_locator_light(
    v4_lights: V4LightConfig,
    roi_locator: RoiLocatorConfig,
    light_order: tuple[str, ...],
) -> None:
    dome_light = v4_lights.semantic_to_light_id.get(roi_locator.dome_semantic_light, roi_locator.dome_semantic_light)
    if dome_light not in light_order:
        raise RecipeValidationError(
            f"roi_locator.dome_semantic_light 映射光源不在 light_order 中: "
            f"{roi_locator.dome_semantic_light}->{dome_light}"
        )
    if roi_locator.backend in {"fake_yolo", "onnx_yolo", "onnx_yolo_seg"} and roi_locator.model_path in (None, ""):
        raise RecipeValidationError(f"roi_locator.backend={roi_locator.backend} 必须配置 model_path")


def _validate_registration_lights(
    light_order: tuple[str, ...],
    required_lights: tuple[str, ...],
    registration: RegistrationConfig,
) -> None:
    for field_name, light_id in (
        ("registration.base_light_id", registration.base_light_id),
        ("registration.base_light_fallback", registration.base_light_fallback),
    ):
        if light_id not in light_order:
            raise RecipeValidationError(f"{field_name} 不在 light_order 中: {light_id}")
        if light_id not in required_lights:
            raise RecipeValidationError(f"{field_name} 必须属于 quality.required_lights: {light_id}")


def _validate_camera_lights(
    cameras: tuple[CameraRecipe, ...],
    required_lights: tuple[str, ...],
    registration: RegistrationConfig,
) -> None:
    seen_views: set[tuple[str, str]] = set()
    for camera in cameras:
        key = (camera.camera_id, camera.pose_id)
        if key in seen_views:
            raise RecipeValidationError(f"重复视角配置: camera_id={camera.camera_id} pose_id={camera.pose_id}")
        seen_views.add(key)
        missing = [light_id for light_id in required_lights if light_id not in camera.light_order]
        if missing:
            raise RecipeValidationError(f"cameras.{camera.camera_id}.light_order 缺少 required_lights: {missing}")
        if camera.base_light_id not in camera.light_order:
            raise RecipeValidationError(
                f"cameras.{camera.camera_id}.base_light_id 不在该机位 light_order 中: {camera.base_light_id}"
            )
        if camera.base_light_id not in required_lights:
            raise RecipeValidationError(
                f"cameras.{camera.camera_id}.base_light_id 必须属于 quality.required_lights: {camera.base_light_id}"
            )
        if registration.base_light_fallback not in camera.light_order:
            raise RecipeValidationError(
                f"registration.base_light_fallback 不在 cameras.{camera.camera_id}.light_order 中: "
                f"{registration.base_light_fallback}"
            )


def _validate_model_refs(cameras: tuple[CameraRecipe, ...], models: dict[str, ModelConfig]) -> None:
    for camera in cameras:
        _validate_model_ref(models, camera.model_key, f"机位 {camera.camera_id}", expected_role="primary")
        if camera.safety_net_model_key is not None:
            _validate_model_ref(
                models,
                camera.safety_net_model_key,
                f"机位 {camera.camera_id} safety_net_model_key",
                expected_role="safety_net",
            )
        for roi_name, model_key in camera.roi_models.items():
            _validate_model_ref(models, model_key, f"机位 {camera.camera_id} ROI {roi_name}", expected_role="primary")
        for roi_name, model_key in camera.roi_safety_net_models.items():
            _validate_model_ref(
                models,
                model_key,
                f"机位 {camera.camera_id} ROI {roi_name} safety_net",
                expected_role="safety_net",
            )


def _validate_model_ref(models: dict[str, ModelConfig], model_key: str, location: str, expected_role: str) -> None:
    model = models.get(model_key)
    if model is None:
        raise RecipeValidationError(f"{location} 引用了不存在的模型: {model_key}")
    if model.role != expected_role:
        raise RecipeValidationError(f"{location} 引用的模型角色必须是 {expected_role}: {model_key}")


def _validate_model_thresholds(models: dict[str, ModelConfig], thresholds: dict[str, ThresholdConfig]) -> None:
    for model_key, model in models.items():
        missing = [class_name for class_name in model.class_names if class_name not in thresholds]
        if missing:
            raise RecipeValidationError(f"models.{model_key}.class_names 缺少显式 thresholds 配置: {missing}")
        if model.backend == "patchcore_knn":
            if model.memory_bank_path in (None, ""):
                raise RecipeValidationError(f"models.{model_key}.backend=patchcore_knn 必须配置 memory_bank_path")
            if model.embedding_backend == "none":
                raise RecipeValidationError(f"models.{model_key}.backend=patchcore_knn 必须配置 embedding_backend")


def _required_str(data: dict[str, Any], key: str) -> str:
    if key not in data:
        raise RecipeValidationError(f"缺少必填字段: {key}")
    return _str(data[key], key)


def _optional_str(value: Any, name: str) -> str | None:
    if value in (None, ""):
        return None
    return _str(value, name)


def _str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise RecipeValidationError(f"{name} 必须是非空字符串")
    return value


def _decision(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"RECHECK", "ERROR", "NG"}:
        raise RecipeValidationError(f"{name} 必须是 RECHECK、ERROR 或 NG")
    return value


def _output_decode(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"none", "detection_rows", "ultralytics_yolo"}:
        raise RecipeValidationError(f"{name} 必须是 none、detection_rows 或 ultralytics_yolo")
    return value


def _bbox_format(value: Any, name: str) -> str:
    value = _str(value, name)
    if value not in {"xyxy_pixel", "xyxy_normalized"}:
        raise RecipeValidationError(f"{name} 必须是 xyxy_pixel 或 xyxy_normalized")
    return value


def _dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RecipeValidationError(f"{name} 必须是字典")
    return value


def _str_tuple(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise RecipeValidationError(f"{name} 必须是字符串列表")
    result = tuple(_str(item, name) for item in value)
    if not result:
        raise RecipeValidationError(f"{name} 不能为空")
    return result


def _unique_str_tuple(value: Any, name: str) -> tuple[str, ...]:
    result = _str_tuple(value, name)
    duplicated = sorted({item for item in result if result.count(item) > 1})
    if duplicated:
        raise RecipeValidationError(f"{name} 存在重复项: {duplicated}")
    return result


def _optional_unique_str_tuple(value: Any, name: str) -> tuple[str, ...]:
    if value in (None, ()):
        return ()
    if isinstance(value, list) and not value:
        return ()
    return _unique_str_tuple(value, name)


def _float(value: Any, name: str) -> float:
    if not isinstance(value, (int, float)):
        raise RecipeValidationError(f"{name} 必须是数字")
    return float(value)


def _positive_float(value: Any, name: str) -> float:
    result = _float(value, name)
    if result <= 0:
        raise RecipeValidationError(f"{name} 必须大于 0")
    return result


def _non_negative_float(value: Any, name: str) -> float:
    result = _float(value, name)
    if result < 0:
        raise RecipeValidationError(f"{name} 必须大于等于 0")
    return result


def _ratio(value: Any, name: str) -> float:
    result = _float(value, name)
    if result < 0 or result > 1:
        raise RecipeValidationError(f"{name} 必须在 [0, 1] 范围内")
    return result


def _gray_value(value: Any, name: str) -> float:
    result = _float(value, name)
    if result < 0 or result > 255:
        raise RecipeValidationError(f"{name} 必须在 [0, 255] 范围内")
    return result


def _optional_float(value: Any, name: str) -> float | None:
    if value is None:
        return None
    return _float(value, name)


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int):
        raise RecipeValidationError(f"{name} 必须是整数")
    return value


def _positive_int(value: Any, name: str) -> int:
    result = _int(value, name)
    if result <= 0:
        raise RecipeValidationError(f"{name} 必须大于 0")
    return result


def _non_negative_int(value: Any, name: str) -> int:
    result = _int(value, name)
    if result < 0:
        raise RecipeValidationError(f"{name} 必须大于等于 0")
    return result
