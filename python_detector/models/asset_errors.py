from __future__ import annotations

from typing import Any


class ModelAssetUnavailableError(RuntimeError):
    """模型或模型依赖资产未就绪，当前任务应保守复检并保存样本。"""

    def __init__(self, message: str, *, asset_kind: str, asset_path: str, reason: str) -> None:
        super().__init__(message)
        self.message = message
        self.asset_kind = asset_kind
        self.asset_path = asset_path
        self.reason = reason

    def context(self) -> dict[str, Any]:
        return {
            "type": self.__class__.__name__,
            "message": self.message,
            "asset_kind": self.asset_kind,
            "asset_path": self.asset_path,
            "reason": self.reason,
        }
