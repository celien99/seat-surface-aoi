from __future__ import annotations


class TrainingDataError(RuntimeError):
    """数据集为空、标注缺失、图片缺失。"""


class OnnxExportError(RuntimeError):
    """ONNX 导出失败。"""


class EmbeddingExtractionError(RuntimeError):
    """embedding 推理失败。"""


class DimensionMismatchError(RuntimeError):
    """输入/输出维度不一致。"""


class EmptyMemoryBankError(RuntimeError):
    """memory bank 无向量。"""


class ModelValidationError(RuntimeError):
    """训练产物校验失败。"""
