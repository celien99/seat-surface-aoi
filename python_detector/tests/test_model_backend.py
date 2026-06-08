from dataclasses import replace

import pytest

from python_detector.config.recipe_schema import ModelConfig, RecipeManager
from python_detector.models.inference_engine import FakeModel, InferenceEngine, ModelRegistry
from python_detector.pipeline.feature_builder import FeatureGroup


def _feature_group() -> FeatureGroup:
    return FeatureGroup(
        sequence_id=1,
        camera_id="TOP_BACK",
        roi_name="full",
        model_key="fake_default",
        features={"ch4_high_max_min": [0] * 64},
    )


def test_fake_model_modes_cover_ok_recheck_ng() -> None:
    group = _feature_group()
    assert FakeModel("ok").run(group) == []
    assert FakeModel("recheck").run(group)[0].score == 0.22
    assert FakeModel("ng").run(group)[0].score == 0.88


def test_onnx_missing_model_path_fails_conservatively() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, models={"fake_default": ModelConfig(backend="onnx", model_path="missing.onnx")})
    with pytest.raises(RuntimeError):
        InferenceEngine(ModelRegistry()).infer([_feature_group()], recipe)


def test_missing_model_key_does_not_fallback_to_default_ok() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    missing_group = replace(_feature_group(), model_key="missing_model")
    with pytest.raises(RuntimeError):
        InferenceEngine(ModelRegistry()).infer([missing_group], recipe)
