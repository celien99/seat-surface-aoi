"""配方校验函数和类型检查原语，从 recipe_schema.py 抽取以控制文件规模。"""

from __future__ import annotations

from typing import Any

from python_detector.config.schema_types import (
    CameraRecipe,
    ModelConfig,
    RecipeValidationError,
    RegistrationConfig,
    RoiLocatorConfig,
    V4LightConfig,
)


# ---------------------------------------------------------------------------
# 配方级校验函数
# ---------------------------------------------------------------------------


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
    dome_light = v4_lights.semantic_to_light_id.get(
        roi_locator.dome_semantic_light, roi_locator.dome_semantic_light
    )
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


def _validate_model_ref(
    models: dict[str, ModelConfig],
    model_key: str,
    location: str,
    expected_role: str,
) -> None:
    model = models.get(model_key)
    if model is None:
        raise RecipeValidationError(f"{location} 引用了不存在的模型: {model_key}")
    if model.role != expected_role:
        raise RecipeValidationError(f"{location} 引用的模型角色必须是 {expected_role}: {model_key}")


def _validate_model_configs(models: dict[str, ModelConfig]) -> None:
    for model_key, model in models.items():
        if model.backend == "patchcore_knn":
            if model.memory_bank_path in (None, ""):
                raise RecipeValidationError(
                    f"models.{model_key}.backend=patchcore_knn 必须配置 memory_bank_path"
                )
            if model.embedding_backend == "none":
                raise RecipeValidationError(
                    f"models.{model_key}.backend=patchcore_knn 必须配置 embedding_backend"
                )
            if model.spatial_mode:
                if model.embedding_backend != "onnx_wideresnet50":
                    raise RecipeValidationError(
                        f"models.{model_key}.spatial_mode=True 必须使用 embedding_backend=onnx_wideresnet50"
                    )
                if not model.spatial_layers:
                    raise RecipeValidationError(
                        f"models.{model_key}.spatial_mode=True 必须配置 spatial_layers"
                    )


# ---------------------------------------------------------------------------
# 类型检查原语（供 _*_from_dict 使用）
# ---------------------------------------------------------------------------


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
