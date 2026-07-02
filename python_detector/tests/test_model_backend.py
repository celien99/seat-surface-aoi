from dataclasses import replace

import numpy as np
import pytest

from python_detector.config.recipe_schema import ModelConfig, RecipeManager
from python_detector.models.inference_engine import (
    FakeModel,
    InferenceEngine,
    ModelAssetUnavailableInferenceError,
    ModelInferenceError,
    ModelRegistry,
    OnnxModel,
)
from python_detector.models.patchcore_model import PatchCoreModel
from python_detector.models.embedding import SpatialEmbedding
from python_detector.models.patchcore import PatchCoreThresholds, SpatialAnomalyScore
from python_detector.models.pca import PcaProjector
from python_detector.models.yolo_decode import decode_yolo_rows, decode_yolo_segmentation
from python_detector.pipeline.feature_builder import FeatureGroup


def _feature_group() -> FeatureGroup:
    return FeatureGroup(
        sequence_id=1,
        camera_id="TOP_BACK",
        roi_name="seat",
        model_key="fake_default",
        features={
            "ch0_diffuse": [10] * 64,
            "ch1_polar_diffuse": [20] * 64,
            "ch2_high_left": [30] * 64,
        },
        roi_bbox_xyxy_pixel=(10, 20, 73, 67),
        feature_shape_hw=(48, 64),
        tensor_nchw=[
            [
                [[0.1 for _ in range(64)] for _ in range(48)],
                [[0.2 for _ in range(64)] for _ in range(48)],
                [[0.3 for _ in range(64)] for _ in range(48)],
            ]
        ],
        tensor_channel_names=("ch0_diffuse", "ch1_polar_diffuse", "ch2_high_left"),
        evidence_lights_by_channel={
            "ch0_diffuse": ("DIFFUSE",),
            "ch1_polar_diffuse": ("POLAR_DIFFUSE",),
            "ch2_high_left": ("HIGH_LEFT",),
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


class _SpatialExtractor:
    def __init__(self) -> None:
        self.patch_embeddings = np.asarray([[0.0, 0.0], [1.0, 1.0]], dtype=np.float32)

    def extract_spatial(self, feature_group, config):  # type: ignore[no-untyped-def]
        return SpatialEmbedding(
            patch_embeddings=self.patch_embeddings,
            spatial_shape=(1, 2),
            patch_dim=2,
            backend="test_spatial",
            version="test_v1",
            layer_names=("layer2",),
            input_shape_nchw=feature_group.tensor_shape_nchw(),
            layer_shapes={"layer2": (2, 1, 2)},
        )


class _SpatialKnn:
    def __init__(self, anomaly_map) -> None:  # type: ignore[no-untyped-def]
        self.anomaly_map = anomaly_map

    def score_spatial(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        anomaly_map = np.asarray(self.anomaly_map, dtype=np.float32)
        return SpatialAnomalyScore(
            anomaly_map=anomaly_map,
            spatial_shape=(1, 2),
            nearest_distances=anomaly_map,
            memory_bank_size=1,
            embedding_dim=2,
            backend="exact_knn",
            version="bank_v1",
            thresholds=PatchCoreThresholds(recheck_score=0.1, ng_score=0.5),
            faiss_index_path=None,
            fallback_reason=None,
        )


def test_fake_model_modes_cover_ok_recheck_ng() -> None:
    group = _feature_group()
    assert FakeModel("ok").run(group) == []
    assert FakeModel("recheck").run(group)[0].score == 0.22
    assert FakeModel("ng").run(group)[0].score == 0.88


def test_onnx_missing_model_path_fails_conservatively() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, models={"fake_default": ModelConfig(backend="onnx", model_path="missing.onnx")})
    with pytest.raises(ModelAssetUnavailableInferenceError) as exc_info:
        InferenceEngine(ModelRegistry()).infer([_feature_group()], recipe)
    assert exc_info.value.context() == {
        "type": "ModelAssetUnavailableInferenceError",
        "message": "TOP_BACK/seat/fake_default: 模型资产未就绪，保存采集样本: ONNX detection 模型文件不存在: missing.onnx",
        "model_key": "fake_default",
        "backend": "onnx",
        "camera_id": "TOP_BACK",
        "roi_name": "seat",
        "tensor_shape_nchw": [1, 3, 48, 64],
        "cause_type": "ModelAssetUnavailableError",
        "asset_unavailable": True,
        "asset": {
            "type": "ModelAssetUnavailableError",
            "message": "ONNX detection 模型文件不存在: missing.onnx",
            "asset_kind": "onnx_model",
            "asset_path": "missing.onnx",
            "reason": "missing",
        },
    }


def test_onnx_placeholder_model_path_fails_before_session_creation(tmp_path) -> None:
    placeholder = tmp_path / "placeholder.onnx"
    placeholder.write_text("\n", encoding="utf-8")
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    recipe = replace(recipe, models={"fake_default": ModelConfig(backend="onnx", model_path=str(placeholder))})

    with pytest.raises(ModelInferenceError) as exc_info:
        InferenceEngine(ModelRegistry()).infer([_feature_group()], recipe)

    assert "模型文件为空或仍是占位文件" in str(exc_info.value)


def test_missing_model_key_does_not_fallback_to_default_ok() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    missing_group = replace(_feature_group(), model_key="missing_model")
    with pytest.raises(ModelInferenceError) as exc_info:
        InferenceEngine(ModelRegistry()).infer([missing_group], recipe)
    assert exc_info.value.model_key == "missing_model"
    assert exc_info.value.backend == "missing"


def test_model_registry_cache_is_scoped_by_full_model_config() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    registry = ModelRegistry()
    ok_recipe = replace(
        recipe,
        models={
            "fake_default": replace(recipe.models["fake_default"], fake_mode="ok"),
        },
    )
    ng_recipe = replace(
        recipe,
        models={
            "fake_default": replace(recipe.models["fake_default"], fake_mode="ng"),
        },
    )

    ok_model = registry.get_model("fake_default", ok_recipe)
    ng_model = registry.get_model("fake_default", ng_recipe)

    assert ok_model is not ng_model
    assert ok_model.run(_feature_group()) == []
    assert ng_model.run(_feature_group())[0].score == 0.88


def test_onnx_detection_rows_decode_maps_normalized_roi_bbox() -> None:
    config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="detection_rows",
        bbox_format="xyxy_normalized",
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
    assert candidates[0].score == pytest.approx(0.91)
    assert candidates[0].bbox_xyxy_pixel == (26, 32, 42, 44)
    assert candidates[0].area_px == 17 * 13
    assert candidates[0].evidence_lights == ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT"]
    assert model.session.last_inputs["input"][0][0][0][0] == pytest.approx(0.1)


def test_onnx_ultralytics_yolo_decode_maps_transposed_output() -> None:
    model = object.__new__(OnnxModel)
    model.config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="ultralytics_yolo",
        bbox_format="xyxy_pixel",
        score_threshold=0.3,
    )
    model.session = _Session(
        [
            [
                [32.0, 20.0],
                [24.0, 10.0],
                [10.0, 8.0],
                [12.0, 6.0],
                [0.91, 0.10],
                [0.05, 0.86],
            ]
        ]
    )

    candidates = model.run(_feature_group())

    assert len(candidates) == 2
    assert candidates[0].bbox_xyxy_pixel == (37, 38, 47, 50)


def test_decode_ultralytics_yolo_filters_and_maps_candidates() -> None:
    output = np.asarray(
        [
            [
                [32.0, 20.0, 12.0],
                [24.0, 10.0, 10.0],
                [10.0, 8.0, 4.0],
                [12.0, 6.0, 4.0],
                [0.91, 0.10, 0.20],
                [0.05, 0.86, 0.25],
            ]
        ],
        dtype=np.float32,
    )

    rows = decode_yolo_rows(output, confidence_threshold=0.3, output_decode="ultralytics_yolo")

    np.testing.assert_allclose(
        np.asarray(rows, dtype=np.float32),
        np.asarray(
            [
                [27.0, 18.0, 37.0, 30.0, 0.91, 0.0],
                [16.0, 7.0, 24.0, 13.0, 0.86, 1.0],
            ],
            dtype=np.float32,
        ),
    )


def test_decode_ultralytics_yolo_rejects_nonfinite_scores() -> None:
    output = np.asarray([[[32.0], [24.0], [10.0], [12.0], [np.nan]]], dtype=np.float32)

    with pytest.raises(RuntimeError, match="非有限"):
        decode_yolo_rows(output, confidence_threshold=0.3, output_decode="ultralytics_yolo")


def test_decode_ultralytics_yolo_preserves_threshold_boundary() -> None:
    output = np.asarray([[[32.0], [24.0], [10.0], [12.0], [0.3]]], dtype=np.float32)

    rows = decode_yolo_rows(output, confidence_threshold=0.30000002, output_decode="ultralytics_yolo")

    assert rows == []


def test_decode_segmentation_rows_filters_and_thresholds_masks() -> None:
    output = np.asarray(
        [
            [1.0, 2.0, 4.0, 5.0, 0.92, 1.0, 0.0, 0.7, 0.8, 0.1],
            [2.0, 3.0, 5.0, 6.0, 0.20, 0.0, np.nan, np.nan, np.nan, np.nan],
        ],
        dtype=np.float32,
    )

    candidates = decode_yolo_segmentation(
        [output],
        confidence_threshold=0.5,
        mask_threshold=0.5,
        output_decode="segmentation_rows",
    )

    assert len(candidates) == 1
    assert candidates[0].bbox_xyxy == pytest.approx((1.0, 2.0, 4.0, 5.0))
    assert candidates[0].score == pytest.approx(0.92)
    assert candidates[0].class_id == 1
    assert candidates[0].mask.tolist() == [[0, 1], [1, 0]]


def test_decode_segmentation_rows_rejects_nonfinite_scores() -> None:
    output = np.asarray([[1.0, 2.0, 4.0, 5.0, np.nan, 1.0, 0.0, 0.7, 0.8, 0.1]], dtype=np.float32)

    with pytest.raises(RuntimeError, match="非有限"):
        decode_yolo_segmentation(
            [output],
            confidence_threshold=0.5,
            mask_threshold=0.5,
            output_decode="segmentation_rows",
        )


def test_decode_ultralytics_yolo_seg_filters_and_builds_masks() -> None:
    boxes = np.asarray(
        [
            [
                [4.0, 8.0],
                [4.0, 8.0],
                [4.0, 4.0],
                [4.0, 4.0],
                [0.20, 0.10],
                [0.90, 0.25],
                [8.0, -8.0],
                [-8.0, 8.0],
            ]
        ],
        dtype=np.float32,
    )
    protos = np.asarray([[[[1.0, 0.0], [0.0, 1.0]], [[0.0, 1.0], [1.0, 0.0]]]], dtype=np.float32)

    candidates = decode_yolo_segmentation(
        [boxes, protos],
        confidence_threshold=0.5,
        mask_threshold=0.5,
        output_decode="ultralytics_yolo_seg",
    )

    assert len(candidates) == 1
    assert candidates[0].bbox_xyxy == pytest.approx((2.0, 2.0, 6.0, 6.0))
    assert candidates[0].score == pytest.approx(0.90)
    assert candidates[0].class_id == 1
    assert candidates[0].mask.tolist() == [[1, 0], [0, 1]]
    assert candidates[0].mask_bbox_xyxy == pytest.approx((0.0, 0.0, 1.0, 1.0))


def test_decode_ultralytics_yolo_seg_rejects_nonfinite_scores() -> None:
    boxes = np.asarray([[[4.0], [4.0], [4.0], [4.0], [0.20], [np.nan], [8.0], [-8.0]]], dtype=np.float32)
    protos = np.asarray([[[[1.0, 0.0], [0.0, 1.0]], [[0.0, 1.0], [1.0, 0.0]]]], dtype=np.float32)

    with pytest.raises(RuntimeError, match="非有限"):
        decode_yolo_segmentation(
            [boxes, protos],
            confidence_threshold=0.5,
            mask_threshold=0.5,
            output_decode="ultralytics_yolo_seg",
        )


def test_decode_ultralytics_yolo_seg_skips_low_confidence_before_proto_mask_decode() -> None:
    boxes = np.asarray([[[4.0], [4.0], [4.0], [4.0], [0.10], [0.20], [8.0], [-8.0]]], dtype=np.float32)
    protos = np.asarray([[[[np.nan, np.nan], [np.nan, np.nan]], [[np.nan, np.nan], [np.nan, np.nan]]]], dtype=np.float32)

    candidates = decode_yolo_segmentation(
        [boxes, protos],
        confidence_threshold=0.5,
        mask_threshold=0.5,
        output_decode="ultralytics_yolo_seg",
    )

    assert candidates == []


def test_onnx_detection_rows_maps_full_normalized_bbox_inside_roi() -> None:
    model = object.__new__(OnnxModel)
    model.config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="detection_rows",
        bbox_format="xyxy_normalized",
        score_threshold=0.3,
    )
    model.session = _Session([[0.0, 0.0, 1.0, 1.0, 0.91, 0]])

    candidate = model.run(_feature_group())[0]

    assert candidate.bbox_xyxy_pixel == (10, 20, 73, 67)
    assert candidate.area_px == 64 * 48


def test_onnx_detection_rows_maps_perspective_roi_bbox_to_source() -> None:
    config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="detection_rows",
        bbox_format="xyxy_pixel",
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


@pytest.mark.parametrize(
    ("bbox_format", "row", "message"),
    [
        ("xyxy_normalized", [-0.1, 0.1, 0.5, 0.5, 0.91, 0], "归一化 bbox 越界"),
        ("xyxy_pixel", [0.0, 0.0, 64.0, 10.0, 0.91, 0], "像素 bbox x 越界"),
        ("xyxy_pixel", [0.0, 0.0, 10.0, 48.0, 0.91, 0], "像素 bbox y 越界"),
        ("xyxy_pixel", [10.0, 0.0, 5.0, 10.0, 0.91, 0], "bbox 坐标反向"),
        ("xyxy_pixel", [float("nan"), 0.0, 5.0, 10.0, 0.91, 0], "非有限值"),
        ("xyxy_pixel", [0.0, 0.0, 5.0, 10.0, 1.2, 0], "score 越界"),
        ("xyxy_pixel", [0.0, 0.0, 5.0, 10.0, float("nan"), 0], "score 越界"),
        ("xyxy_pixel", [0.0, 0.0, 5.0, 10.0, 0.91, 0.5], "class_id 不是整数"),
        ("xyxy_pixel", [0.0, 0.0, 5.0, 10.0, 0.91, -1], "class_id 越界"),
    ],
)
def test_onnx_detection_rows_rejects_invalid_bbox_without_clamping(
    bbox_format: str,
    row: list[float],
    message: str,
) -> None:
    model = object.__new__(OnnxModel)
    model.config = ModelConfig(
        backend="onnx",
        model_path="unused.onnx",
        output_decode="detection_rows",
        bbox_format=bbox_format,
        score_threshold=0.3,
    )
    model.session = _Session([row])

    with pytest.raises(RuntimeError, match=message):
        model.run(_feature_group())


def test_onnx_decode_none_fails_conservatively() -> None:
    model = object.__new__(OnnxModel)
    model.config = ModelConfig(backend="onnx", model_path="unused.onnx", output_decode="none")
    model.session = _Session([[0, 0, 1, 1, 0.9, 0]])
    with pytest.raises(RuntimeError, match="输出解码未配置"):
        model.run(_feature_group())


def test_patchcore_spatial_squeezes_singleton_anomaly_map_without_numpy_truth_error() -> None:
    model = PatchCoreModel(
        ModelConfig(
            backend="patchcore_knn",
            model_family="patchcore",
            embedding_backend="onnx_wideresnet50",
            embedding_model_path="unused.onnx",
            memory_bank_path="unused_bank.json",
            score_threshold=0.1,
            spatial_mode=True,
            spatial_layers=("layer2",),
        ),
        embedding_extractor=_SpatialExtractor(),  # type: ignore[arg-type]
        knn_index=_SpatialKnn([[[0.0, 0.9]]]),  # type: ignore[arg-type]
    )

    candidates = model.run(_feature_group())

    assert candidates
    assert candidates[0].score == pytest.approx(0.9)


def test_patchcore_spatial_rejects_multi_channel_anomaly_map_without_numpy_truth_error() -> None:
    model = PatchCoreModel(
        ModelConfig(
            backend="patchcore_knn",
            model_family="patchcore",
            embedding_backend="onnx_wideresnet50",
            embedding_model_path="unused.onnx",
            memory_bank_path="unused_bank.json",
            score_threshold=0.1,
            spatial_mode=True,
            spatial_layers=("layer2",),
        ),
        embedding_extractor=_SpatialExtractor(),  # type: ignore[arg-type]
        knn_index=_SpatialKnn([[[0.0, 0.9]], [[0.1, 0.2]]]),  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match="anomaly_map 必须是 2 维矩阵"):
        model.run(_feature_group())


def test_patchcore_spatial_uses_numpy_max_for_2d_anomaly_map() -> None:
    model = PatchCoreModel(
        ModelConfig(
            backend="patchcore_knn",
            model_family="patchcore",
            embedding_backend="onnx_wideresnet50",
            embedding_model_path="unused.onnx",
            memory_bank_path="unused_bank.json",
            score_threshold=0.1,
            spatial_mode=True,
            spatial_layers=("layer2",),
        ),
        embedding_extractor=_SpatialExtractor(),  # type: ignore[arg-type]
        knn_index=_SpatialKnn([[0.0, 0.9]]),  # type: ignore[arg-type]
    )

    candidates = model.run(_feature_group())

    assert candidates
    assert candidates[0].score == pytest.approx(0.9)
    assert model.config.spatial_mode is True


def test_patchcore_spatial_uses_bank_thresholds_instead_of_model_config() -> None:
    model = PatchCoreModel(
        ModelConfig(
            backend="patchcore_knn",
            model_family="patchcore",
            embedding_backend="onnx_wideresnet50",
            embedding_model_path="unused.onnx",
            memory_bank_path="unused_bank.json",
            score_threshold=0.95,
            spatial_mode=True,
            spatial_layers=("layer2",),
        ),
        embedding_extractor=_SpatialExtractor(),  # type: ignore[arg-type]
        knn_index=_SpatialKnn([[0.0, 0.6]]),  # type: ignore[arg-type]
    )

    candidates = model.run(_feature_group())

    assert len(candidates) == 1
    assert candidates[0].score == pytest.approx(0.6)
    assert candidates[0].recheck_score == pytest.approx(0.1)
    assert candidates[0].ng_score == pytest.approx(0.5)


def test_pca_project_batch_accepts_numpy_matrix_without_truth_value_error(tmp_path) -> None:
    pca_path = tmp_path / "pca.json"
    pca_path.write_text(
        '{"version":"pca_v1","mean":[1.0,2.0],"components":[[1.0,0.0],[0.0,1.0]]}',
        encoding="utf-8",
    )

    projected, version, input_dim, output_dim = PcaProjector().project_batch(
        np.asarray([[2.0, 4.0], [0.0, 2.5]], dtype=np.float32),
        str(pca_path),
        "pca_v1",
    )

    assert version == "pca_v1"
    assert input_dim == 2
    assert output_dim == 2
    assert projected.dtype == np.float32
    np.testing.assert_allclose(projected, np.asarray([[1.0, 2.0], [-1.0, 0.5]], dtype=np.float64))
