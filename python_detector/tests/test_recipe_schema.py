import pytest

from python_detector.config.recipe_schema import RecipeManager, RecipeValidationError, recipe_from_dict


def test_recipe_manager_loads_default_yaml() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.recipe_id == "seat_a_black_leather_v1"
    assert recipe.cameras[0].camera_id == "TOP_BACK"
    assert recipe.models["fake_default"].backend == "fake"


def test_recipe_rejects_missing_required_field() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict({"recipe_id": "bad"})


def test_recipe_rejects_required_light_not_in_light_order() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad",
                "sku": "sku",
                "light_order": ["DIFFUSE"],
                "quality": {"required_lights": ["POLAR_DIFFUSE"]},
                "cameras": {"TOP": {"light_order": ["DIFFUSE"]}},
            }
        )


def test_recipe_rejects_patchcore_as_primary_detector() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_patchcore",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"model_key": "patchcore_primary"}},
                "models": {
                    "patchcore_primary": {
                        "backend": "fake",
                        "model_family": "patchcore",
                        "role": "primary",
                    }
                },
            }
        )


def test_recipe_rejects_safety_net_as_primary_roi_model() -> None:
    with pytest.raises(RecipeValidationError):
        recipe_from_dict(
            {
                "recipe_id": "bad_safety_net_ref",
                "sku": "sku",
                "light_order": ["DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT"],
                "cameras": {"TOP": {"roi_models": {"full": "unknown_safety_net"}}},
                "models": {
                    "unknown_safety_net": {
                        "backend": "fake",
                        "model_family": "patchcore",
                        "role": "safety_net",
                    }
                },
            }
        )


def test_recipe_accepts_roi_primary_and_safety_net_models() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    assert recipe.model_key_for("TOP_BACK", "full") == "fake_default"
    assert recipe.safety_net_model_keys_for("TOP_BACK", "full") == ("unknown_safety_net",)
