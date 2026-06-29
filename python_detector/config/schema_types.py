"""座椅 AOI 配方数据模型：纯 frozen dataclass 定义，不包含加载和校验逻辑。"""

from __future__ import annotations

from dataclasses import dataclass, field


class RecipeValidationError(ValueError):
    """配方校验失败。"""


@dataclass(frozen=True)
class V4LightConfig:
    semantic_to_light_id: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RoiLocatorConfig:
    backend: str = "template"
    dome_semantic_light: str = "DOME"
    model_path: str | None = None
    min_confidence: float = 0.5
    max_pose_error_px: float = 4.0
    mask_threshold: float = 0.5
    min_mask_area_px: int = 1
    min_mask_area_ratio: float = 0.0
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
    required_lights: tuple[str, ...] = ()
    max_saturation_ratio: float = 0.01
    max_dark_ratio: float = 0.01
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
    max_pose_delta: float = 1e-4
    """机器人位姿 (TCP/RPY) 比较容差，默认 1e-4 等同严格相等。"""


@dataclass(frozen=True)
class RegistrationConfig:
    base_light_id: str = ""
    base_light_fallback: str = ""
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
    base_light_id: str = ""
    light_order: tuple[str, ...] = ()
    roi_models: dict[str, str] = field(default_factory=dict)
    roi_safety_net_models: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CameraDefaults:
    model_key: str = "default"
    safety_net_model_key: str | None = None
    roi_template: str = "python_detector/config/roi/default_roi.yaml"
    calibration_id: str = "calib/simulated_v1"
    base_light_id: str = ""
    light_order: tuple[str, ...] = ()
    roi_models: dict[str, str] = field(default_factory=dict)
    roi_safety_net_models: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DecisionThresholdConfig:
    ng_score: float = 0.35
    recheck_score: float = 0.20
    min_area_px: int = 1
    min_aspect_ratio: float = 0.0
    """bbox 允许的最小长宽比 (w/h)，0 表示不限制。用于过滤长条形噪声。"""
    max_aspect_ratio: float = 0.0
    """bbox 允许的最大长宽比 (w/h)，0 表示不限制。用于过滤长条形噪声。"""


@dataclass(frozen=True)
class FusionConfig:
    iou_threshold: float = 0.5
    max_candidates_per_roi: int = 16


@dataclass(frozen=True)
class ModelConfig:
    backend: str = "fake"
    model_path: str | None = None
    fake_mode: str = "auto"
    model_family: str = "supervised"
    role: str = "primary"
    input_channels: tuple[str, ...] = ()
    input_scale: float = 255.0
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
    spatial_mode: bool = False
    spatial_layers: tuple[str, ...] = ()
    spatial_upsample_height: int = 256
    spatial_upsample_width: int = 256
    anomaly_binarize_min_ratio: float = 0.5
    """anomaly_map 二值化阈值下限：max(score_threshold * min_ratio, max_anomaly * relative)，控制最低灵敏度。"""
    anomaly_binarize_relative: float = 0.3
    """anomaly_map 二值化相对阈值系数：max(score_threshold * min_ratio, max_anomaly * relative)，控制与峰值的相对灵敏度。"""


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
    light_order: tuple[str, ...] = ()
    v4_lights: V4LightConfig = field(default_factory=V4LightConfig)
    camera_defaults: CameraDefaults = field(default_factory=CameraDefaults)
    cameras: tuple[CameraRecipe, ...] = field(default_factory=tuple)
    quality: QualityConfig = field(default_factory=QualityConfig)
    roi_locator: RoiLocatorConfig = field(default_factory=RoiLocatorConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)
    fusion: FusionConfig = field(default_factory=FusionConfig)
    decision_threshold: DecisionThresholdConfig = field(default_factory=DecisionThresholdConfig)
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
