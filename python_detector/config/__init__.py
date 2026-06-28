"""配方、标定和检测配置辅助模块。"""

from python_detector.config.calibration_manager import Calibration, CalibrationManager, RoiTemplate
from python_detector.config.recipe_schema import (
    CameraRecipe,
    DecisionThresholdConfig,
    FusionConfig,
    ModelConfig,
    QualityConfig,
    Recipe,
    RecipeManager,
    RecipeValidationError,
    RegistrationConfig,
    RoiLocatorConfig,
    TraceConfig,
    V4LightConfig,
    load_recipe_file,
    recipe_from_dict,
)

__all__ = [
    "Calibration",
    "CalibrationManager",
    "CameraRecipe",
    "DecisionThresholdConfig",
    "FusionConfig",
    "ModelConfig",
    "QualityConfig",
    "Recipe",
    "RecipeManager",
    "RecipeValidationError",
    "RegistrationConfig",
    "RoiLocatorConfig",
    "RoiTemplate",
    "TraceConfig",
    "V4LightConfig",
    "load_recipe_file",
    "recipe_from_dict",
]
