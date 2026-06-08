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

