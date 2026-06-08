from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QualityConfig:
    required_lights: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    max_saturation_ratio: float = 0.01
    min_mean_gray: float = 20.0
    max_mean_gray: float = 235.0
    min_sharpness: float = 1.0
    max_registration_error_px: float = 1.5


@dataclass(frozen=True)
class RegistrationConfig:
    base_light_id: str = "POLAR_DIFFUSE"
    base_light_fallback: str = "DIFFUSE"
    fail_policy: str = "RECHECK"


@dataclass(frozen=True)
class Recipe:
    recipe_id: str
    sku: str
    light_order: tuple[str, ...] = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")
    quality: QualityConfig = field(default_factory=QualityConfig)
    registration: RegistrationConfig = field(default_factory=RegistrationConfig)


class RecipeManager:
    def __init__(self) -> None:
        self._recipes = {
            "seat_a_black_leather_v1": Recipe(
                recipe_id="seat_a_black_leather_v1",
                sku="seat_a_black_leather",
            )
        }

    def load(self, recipe_id: str) -> Recipe:
        if recipe_id in self._recipes:
            return self._recipes[recipe_id]
        return Recipe(recipe_id=recipe_id, sku="unknown")

