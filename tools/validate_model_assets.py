from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import ModelConfig, Recipe, load_recipe_file
from python_detector.models.patchcore import PatchCoreKnnIndex
from python_detector.models.pca import PcaProjector
from python_detector.paths import DEFAULT_CONFIG_DIR


REPO_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AssetIssue:
    level: str
    location: str
    message: str


def validate_recipe_model_assets(recipe: Recipe) -> list[AssetIssue]:
    issues: list[AssetIssue] = []
    if recipe.roi_locator.backend in {"onnx_yolo", "onnx_yolo_seg"}:
        label = "YOLO ROI segmentation ONNX" if recipe.roi_locator.backend == "onnx_yolo_seg" else "YOLO ROI ONNX"
        issues.extend(_validate_binary_file(recipe.roi_locator.model_path, "roi_locator.model_path", label))
    for model_key, model in recipe.models.items():
        issues.extend(_validate_model_asset(model_key, model))
    return issues


def load_recipe_by_id_or_path(recipe_value: str) -> Recipe:
    path = Path(recipe_value)
    if path.exists():
        return load_recipe_file(path)
    candidate_names = [
        f"{recipe_value}.yaml",
        f"{recipe_value}.example.yaml",
    ]
    for name in candidate_names:
        candidate = DEFAULT_CONFIG_DIR / name
        if candidate.exists():
            return load_recipe_file(candidate)
    for candidate in sorted(DEFAULT_CONFIG_DIR.glob("*.yaml")) + sorted(DEFAULT_CONFIG_DIR.glob("*.example.yaml")):
        recipe = load_recipe_file(candidate)
        if recipe.recipe_id == recipe_value:
            return recipe
    raise FileNotFoundError(f"找不到配方: {recipe_value}")


def _validate_model_asset(model_key: str, model: ModelConfig) -> list[AssetIssue]:
    issues: list[AssetIssue] = []
    prefix = f"models.{model_key}"
    if model.backend == "onnx":
        issues.extend(_validate_binary_file(model.model_path, f"{prefix}.model_path", "ONNX detection"))
    if model.embedding_backend == "onnx_wideresnet50":
        issues.extend(
            _validate_binary_file(
                model.embedding_model_path,
                f"{prefix}.embedding_model_path",
                "WideResNet50 embedding ONNX",
            )
        )
    if model.pca_path:
        issues.extend(_validate_pca_file(model.pca_path, f"{prefix}.pca_path", model.pca_version))
    if model.memory_bank_path:
        issues.extend(_validate_memory_bank(model.memory_bank_path, f"{prefix}.memory_bank_path", model.pca_version))
    if model.faiss_index_path:
        issues.extend(_validate_binary_file(model.faiss_index_path, f"{prefix}.faiss_index_path", "PatchCore FAISS index"))
    return issues


def _validate_binary_file(path_value: str | None, location: str, label: str) -> list[AssetIssue]:
    if not path_value:
        return [AssetIssue("ERROR", location, f"{label} 路径未配置")]
    path = _resolve_repo_path(path_value)
    if not path.exists():
        return [AssetIssue("ERROR", location, f"{label} 文件不存在: {path_value}")]
    if _is_empty_placeholder(path):
        return [AssetIssue("ERROR", location, f"{label} 文件为空或仍是占位文件: {path_value}")]
    return []


def _validate_pca_file(path_value: str, location: str, expected_version: str | None) -> list[AssetIssue]:
    path = _resolve_repo_path(path_value)
    if not path.exists():
        return [AssetIssue("ERROR", location, f"PCA 参数文件不存在: {path_value}")]
    if _is_empty_placeholder(path):
        return [AssetIssue("ERROR", location, f"PCA 参数文件为空或仍是占位文件: {path_value}")]
    try:
        params = PcaProjector().load(str(path))
    except Exception as exc:
        return [AssetIssue("ERROR", location, f"PCA 参数文件无效: {exc}")]
    if expected_version is not None and params.version != expected_version:
        return [AssetIssue("ERROR", location, f"PCA 版本不匹配: {params.version} != {expected_version}")]
    return []


def _validate_memory_bank(path_value: str, location: str, expected_pca_version: str | None) -> list[AssetIssue]:
    path = _resolve_repo_path(path_value)
    if not path.exists():
        return [AssetIssue("ERROR", location, f"PatchCore memory bank 不存在: {path_value}")]
    if _is_empty_placeholder(path):
        return [AssetIssue("ERROR", location, f"PatchCore memory bank 为空或仍是占位文件: {path_value}")]
    try:
        bank = PatchCoreKnnIndex().load(str(path))
    except Exception as exc:
        return [AssetIssue("ERROR", location, f"PatchCore memory bank 无效: {exc}")]
    if expected_pca_version is not None and bank.pca_version not in (None, expected_pca_version):
        return [AssetIssue("ERROR", location, f"PatchCore memory bank PCA 版本不匹配: {bank.pca_version} != {expected_pca_version}")]
    return []


def _resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _is_empty_placeholder(path: Path) -> bool:
    return path.stat().st_size <= 1


def _issue_to_dict(issue: AssetIssue) -> dict[str, Any]:
    return {"level": issue.level, "location": issue.location, "message": issue.message}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验配方引用的真实模型产物文件和元数据")
    parser.add_argument("--recipe", required=True, help="配方 id、yaml 路径或不带后缀的配置名")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出校验结果")
    args = parser.parse_args(argv)

    recipe = load_recipe_by_id_or_path(args.recipe)
    issues = validate_recipe_model_assets(recipe)
    if args.json:
        print(json.dumps({"recipe_id": recipe.recipe_id, "issues": [_issue_to_dict(issue) for issue in issues]}, ensure_ascii=False, indent=2))
    elif issues:
        print(f"模型资产校验失败: recipe_id={recipe.recipe_id}")
        for issue in issues:
            print(f"[{issue.level}] {issue.location}: {issue.message}")
    else:
        print(f"模型资产校验通过: recipe_id={recipe.recipe_id}")
    return 1 if any(issue.level == "ERROR" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
