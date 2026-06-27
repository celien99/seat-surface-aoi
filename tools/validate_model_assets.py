from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import ModelConfig, Recipe, load_recipe_file
from python_detector.models.pca import PcaParameters
from python_detector.models.patchcore import PatchCoreKnnIndex
from python_detector.models.patchcore import PatchCoreBank
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
    issues.extend(_validate_patchcore_chain(prefix, model))
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


def _validate_patchcore_chain(prefix: str, model: ModelConfig) -> list[AssetIssue]:
    if model.backend != "patchcore_knn":
        return []
    issues: list[AssetIssue] = []
    pca = _load_pca_if_valid(model.pca_path)
    bank = _load_bank_if_valid(model.memory_bank_path)
    if pca is not None:
        pca_input_dim = len(pca.mean)
        pca_output_dim = len(pca.components)
        if model.embedding_backend != "none" and pca_input_dim != model.embedding_dim:
            issues.append(
                AssetIssue(
                    "ERROR",
                    f"{prefix}.pca_path",
                    f"PCA 输入维度与模型 embedding_dim 不匹配: {pca_input_dim} != {model.embedding_dim}",
                )
            )
        if bank is not None:
            if bank.embedding_dim != pca_output_dim:
                issues.append(
                    AssetIssue(
                        "ERROR",
                        f"{prefix}.memory_bank_path",
                        f"PCA 输出维度与 PatchCore memory bank 维度不匹配: {pca_output_dim} != {bank.embedding_dim}",
                    )
                )
            if bank.pca_version != model.pca_version:
                issues.append(
                    AssetIssue(
                        "ERROR",
                        f"{prefix}.memory_bank_path",
                        f"PatchCore memory bank PCA 版本必须与配方一致: {bank.pca_version} != {model.pca_version}",
                    )
                )
    elif bank is not None and model.embedding_backend != "none" and bank.embedding_dim != model.embedding_dim:
        issues.append(
            AssetIssue(
                "ERROR",
                f"{prefix}.memory_bank_path",
                f"PatchCore memory bank 维度与模型 embedding_dim 不匹配: {bank.embedding_dim} != {model.embedding_dim}",
            )
        )
    if bank is not None and model.faiss_index_path:
        issues.extend(
            _validate_faiss_index(
                model.faiss_index_path,
                f"{prefix}.faiss_index_path",
                expected_dim=bank.embedding_dim,
                expected_vectors=int(bank.vectors.shape[0]),
            )
        )
    if bank is not None:
        issues.extend(_validate_patchcore_metadata(prefix, model, bank))
    return issues


def _validate_patchcore_metadata(prefix: str, model: ModelConfig, bank: PatchCoreBank) -> list[AssetIssue]:
    metadata = bank.metadata or {}
    if not metadata:
        if not model.spatial_mode and model.embedding_backend != "onnx_wideresnet50":
            return []
        return [
            AssetIssue(
                "WARN",
                f"{prefix}.memory_bank_path",
                "PatchCore memory bank 缺少 metadata，无法校验 input_channels、spatial_shape 和 embedding 模型 hash",
            )
        ]
    issues: list[AssetIssue] = []
    expected_channels = list(model.input_channels)
    actual_channels = metadata.get("input_channels")
    if actual_channels is not None and list(actual_channels) != expected_channels:
        issues.append(
            AssetIssue(
                "ERROR",
                f"{prefix}.memory_bank_path",
                f"PatchCore input_channels 与配方不匹配: {actual_channels} != {expected_channels}",
            )
        )
    expected_spatial_mode = bool(model.spatial_mode)
    actual_spatial_mode = metadata.get("spatial_mode")
    if actual_spatial_mode is not None and bool(actual_spatial_mode) != expected_spatial_mode:
        issues.append(
            AssetIssue(
                "ERROR",
                f"{prefix}.memory_bank_path",
                f"PatchCore spatial_mode 与配方不匹配: {actual_spatial_mode} != {expected_spatial_mode}",
            )
        )
    expected_layers = list(model.spatial_layers)
    actual_layers = metadata.get("spatial_layers")
    if actual_layers is not None and list(actual_layers) != expected_layers:
        issues.append(
            AssetIssue(
                "ERROR",
                f"{prefix}.memory_bank_path",
                f"PatchCore spatial_layers 与配方不匹配: {actual_layers} != {expected_layers}",
            )
        )
    _append_int_metadata_issue(
        issues,
        prefix,
        metadata,
        "spatial_upsample_height",
        int(model.spatial_upsample_height),
    )
    _append_int_metadata_issue(
        issues,
        prefix,
        metadata,
        "spatial_upsample_width",
        int(model.spatial_upsample_width),
    )
    expected_embedding_path = model.embedding_model_path
    expected_hash = metadata.get("embedding_model_sha256")
    if expected_embedding_path and expected_hash:
        path = _resolve_repo_path(expected_embedding_path)
        if path.exists() and not _is_empty_placeholder(path):
            actual_hash = _sha256_file(path)
            if actual_hash != expected_hash:
                issues.append(
                    AssetIssue(
                        "ERROR",
                        f"{prefix}.embedding_model_path",
                        f"WideResNet50 embedding ONNX hash 与 PatchCore bank metadata 不匹配: {actual_hash} != {expected_hash}",
                    )
                )
    return issues


def _append_int_metadata_issue(
    issues: list[AssetIssue],
    prefix: str,
    metadata: dict[str, Any],
    field: str,
    expected: int,
) -> None:
    actual = metadata.get(field)
    if actual is None:
        return
    try:
        actual_int = int(actual)
    except (TypeError, ValueError):
        issues.append(
            AssetIssue(
                "ERROR",
                f"{prefix}.memory_bank_path",
                f"PatchCore {field} metadata 无效: {actual}",
            )
        )
        return
    if actual_int != expected:
        issues.append(
            AssetIssue(
                "ERROR",
                f"{prefix}.memory_bank_path",
                f"PatchCore {field} 与配方不匹配: {actual_int} != {expected}",
            )
        )


def _load_pca_if_valid(path_value: str | None) -> PcaParameters | None:
    if not path_value:
        return None
    path = _resolve_repo_path(path_value)
    if not path.exists() or _is_empty_placeholder(path):
        return None
    try:
        return PcaProjector().load(str(path))
    except Exception:
        return None


def _load_bank_if_valid(path_value: str | None) -> PatchCoreBank | None:
    if not path_value:
        return None
    path = _resolve_repo_path(path_value)
    if not path.exists() or _is_empty_placeholder(path):
        return None
    try:
        return PatchCoreKnnIndex().load(str(path))
    except Exception:
        return None


def _validate_faiss_index(
    path_value: str,
    location: str,
    *,
    expected_dim: int,
    expected_vectors: int,
) -> list[AssetIssue]:
    path = _resolve_repo_path(path_value)
    if not path.exists() or _is_empty_placeholder(path):
        return []
    try:
        import faiss  # type: ignore
    except Exception:
        return [
            AssetIssue(
                "WARN",
                location,
                "faiss-cpu 未安装，跳过 FAISS 维度和向量数校验；在线链路会回退 exact KNN",
            )
        ]
    try:
        index = faiss.read_index(str(path))
    except Exception as exc:
        return [AssetIssue("ERROR", location, f"PatchCore FAISS index 无法读取: {exc}")]
    actual_dim = int(getattr(index, "d"))
    actual_vectors = int(getattr(index, "ntotal"))
    issues: list[AssetIssue] = []
    if actual_dim != expected_dim:
        issues.append(
            AssetIssue(
                "ERROR",
                location,
                f"PatchCore FAISS index 维度与 memory bank 不匹配: {actual_dim} != {expected_dim}",
            )
        )
    if actual_vectors != expected_vectors:
        issues.append(
            AssetIssue(
                "ERROR",
                location,
                f"PatchCore FAISS index 向量数与 memory bank 不匹配: {actual_vectors} != {expected_vectors}",
            )
        )
    return issues


def _resolve_repo_path(path_value: str) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def _is_empty_placeholder(path: Path) -> bool:
    return path.stat().st_size <= 1


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
