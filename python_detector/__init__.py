"""座椅表面 AOI 的 Python 检测算法模块。"""

from python_detector.algorithm import AlgorithmRun, SeatSurfaceAoiAlgorithm
from python_detector.config.recipe_schema import Recipe, RecipeManager, RecipeValidationError
from python_detector.ipc.data_types import CameraBundle, DefectResult, InspectionResult, LightFrame, SeatInspectionJob
from python_detector.pipeline.pipeline import InspectionPipeline

__version__ = "0.1.0"

__all__ = [
    "AlgorithmRun",
    "CameraBundle",
    "DefectResult",
    "InspectionPipeline",
    "InspectionResult",
    "LightFrame",
    "Recipe",
    "RecipeManager",
    "RecipeValidationError",
    "SeatInspectionJob",
    "SeatSurfaceAoiAlgorithm",
]
