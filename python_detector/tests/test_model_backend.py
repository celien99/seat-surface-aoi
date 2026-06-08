from dataclasses import replace

import pytest

from python_detector.config.recipe_schema import ModelConfig, RecipeManager
from python_detector.models.inference_engine import FakeModel, InferenceEngine, ModelRegistry, OnnxModel
from python_detector.pipeline.feature_builder import FeatureGroup


def _feature_group() -> FeatureGroup:
    return FeatureGroup(
        sequence_id=1,
        camera_id="TOP_BACK",
        roi_name="full",
        model_key="fake_default",
        features={"ch4_high_max_min": [0] * 64},
        roi_bbox_xyxy_pixel=(10, 20, 73, 67),
        feature_shape_hw=(48, 64),
        tensor_nchw=[[[[0.1 for _ in range(64)] for _ in range(48)]]],
        tensor_channel_names=("ch0_diffuse", "ch4_high_max_min"),
        evidence_lights_by_channel={
            "ch0_diffuse": ("DIFFUSE",),
            "ch4_high_max_min": ("HIGH_LEFT", "HIGH_RIGHT"),
        },
    )


class _Input:
    name = "input"


class _Session:
    def __init__(self, output):
        self.output = output
        self.last_inputs = None

    def get_inputs(self):
        return [_Input()]

    def run(self, _output_names, inputs):
        self.last_inputs = inputs
        return [self.output]


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


def test_onnx_detection_rows_decode_maps_normalized_roi_bbox() -> None:
    config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="detection_rows",
        bbox_format="xyxy_normalized",
        class_names=("scratch", "dent"),
        score_threshold=0.3,
    )
    model = object.__new__(OnnxModel)
    model.config = config
    model.session = _Session(
        [
            [0.25, 0.25, 0.5, 0.5, 0.91, 1],
            [0.0, 0.0, 1.0, 1.0, 0.10, 0],
        ]
    )

    candidates = model.run(_feature_group())

    assert len(candidates) == 1
    assert candidates[0].class_name == "dent"
    assert candidates[0].score == pytest.approx(0.91)
    assert candidates[0].bbox_xyxy_pixel == (26, 32, 42, 44)
    assert candidates[0].area_px == 17 * 13
    assert candidates[0].evidence_lights == ["DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"]
    assert model.session.last_inputs["input"][0][0][0][0] == pytest.approx(0.1)


def test_onnx_detection_rows_maps_perspective_roi_bbox_to_source() -> None:
    config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="detection_rows",
        bbox_format="xyxy_pixel",
        class_names=("scratch",),
        score_threshold=0.3,
    )
    group = replace(
        _feature_group(),
        roi_bbox_xyxy_pixel=(8, 6, 33, 23),
        feature_shape_hw=(6, 8),
        roi_to_source_matrix=(
            2.5,
            -0.4,
            10.0,
            -0.2,
            3.0,
            8.0,
            0.0,
            0.0,
            1.0,
        ),
    )
    model = object.__new__(OnnxModel)
    model.config = config
    model.session = _Session([[1.0, 1.0, 5.0, 4.0, 0.91, 0]])

    candidates = model.run(group)

    assert candidates[0].bbox_xyxy_pixel == (10, 10, 23, 20)
    assert candidates[0].bbox_xyxy_pixel != (9, 7, 13, 10)


def test_onnx_decode_none_fails_conservatively() -> None:
    model = object.__new__(OnnxModel)
    model.config = ModelConfig(backend="onnx", model_path="unused.onnx", output_decode="none")
    model.session = _Session([[0, 0, 1, 1, 0.9, 0]])
    with pytest.raises(RuntimeError, match="输出解码未配置"):
        model.run(_feature_group())
