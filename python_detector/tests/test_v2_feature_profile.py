from python_detector.config.recipe_schema import RecipeManager
from python_detector.pipeline.feature_builder import FeatureBuilder
from python_detector.pipeline.pipeline import InspectionPipeline
from tools.job_fixture import make_simulated_job


def test_v2_production_feature_profile_uses_five_standard_channels() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    prepared = pipeline.preprocessor.run(make_simulated_job(), recipe)
    cubes = pipeline.reflectance_cube_builder.build(make_simulated_job(), prepared, recipe)
    features = FeatureBuilder().build(cubes, recipe)
    first = features[0]
    assert first.model_key == "fake_default"
    assert {"ch0_diffuse", "ch1_polar_diffuse", "ch2_high_left", "ch3_high_right", "ch4_high_max_min"}.issubset(first.features)
    assert "optional_dark_low_lr_diff" not in first.features


def test_v2_roi_features_include_primary_and_safety_net_models() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    pipeline = InspectionPipeline()
    prepared = pipeline.preprocessor.run(make_simulated_job(), recipe)
    cubes = pipeline.reflectance_cube_builder.build(make_simulated_job(), prepared, recipe)
    features = FeatureBuilder().build(cubes, recipe)
    model_keys = {(group.camera_id, group.roi_name, group.model_key) for group in features}
    assert ("TOP_BACK", "full", "fake_default") in model_keys
    assert ("TOP_BACK", "full", "unknown_safety_net") in model_keys
