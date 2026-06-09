"""模型运行时抽象。"""

from python_detector.models.embedding import EmbeddingExtractor, UnifiedEmbedding
from python_detector.models.inference_engine import (
    DefectCandidate,
    FakeModel,
    InferenceEngine,
    ModelBackend,
    ModelInferenceError,
    ModelRegistry,
    OnnxModel,
    PatchCoreModel,
)
from python_detector.models.patchcore import PatchCoreBank, PatchCoreKnnIndex, PatchCoreScore
from python_detector.models.pca import PcaParameters, PcaProjectionResult, PcaProjector

__all__ = [
    "DefectCandidate",
    "EmbeddingExtractor",
    "FakeModel",
    "InferenceEngine",
    "ModelBackend",
    "ModelInferenceError",
    "ModelRegistry",
    "OnnxModel",
    "PatchCoreBank",
    "PatchCoreKnnIndex",
    "PatchCoreModel",
    "PatchCoreScore",
    "PcaParameters",
    "PcaProjectionResult",
    "PcaProjector",
    "UnifiedEmbedding",
]
