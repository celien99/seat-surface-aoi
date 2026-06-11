from __future__ import annotations

import argparse
import json
from pathlib import Path

from python_detector.config.recipe_schema import ModelConfig, Recipe, RecipeManager
from python_detector.models.embedding import EmbeddingExtractor
from training_tools.dataset_manifest import (
    ManifestSampleGroup,
    build_feature_group_from_manifest_group,
    load_manifest_groups,
)
from training_tools.training_errors import EmbeddingExtractionError, TrainingDataError


def extract_embeddings(
    manifest_path: Path,
    output: Path,
    *,
    recipe_id: str = "seat_a_black_leather_v1",
    model_key: str | None = None,
    embedding_dim: int | None = None,
    backend: str = "statistical",
    model_path: str | None = None,
    channel_order: tuple[str, ...] = (
        "ch0_diffuse", "ch1_polar_diffuse", "ch2_high_left", "ch3_high_right", "ch4_high_max_min",
    ),
    split: str | None = None,
) -> list[dict]:
    try:
        manifest_groups = load_manifest_groups(manifest_path)
    except TrainingDataError as exc:
        if "manifest 没有样本" in str(exc):
            raise TrainingDataError(f"manifest 中没有 OK 样本: {manifest_path}") from exc
        raise
    groups = [
        group
        for group in manifest_groups
        if group.decision == "OK" and group.quality_pass and (split is None or group.split == split)
    ]
    if not groups:
        raise TrainingDataError(f"manifest 中没有 OK 样本: {manifest_path}")

    recipe = RecipeManager().load(recipe_id)
    selected_model_key = model_key or _default_embedding_model_key(recipe)
    model_config = _embedding_model_config(
        recipe.models[selected_model_key],
        backend=backend,
        model_path=model_path,
        embedding_dim=embedding_dim,
        channel_order=channel_order,
    )
    results = _extract_group_embeddings(groups, recipe, selected_model_key, model_config)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) for entry in results) + "\n",
        encoding="utf-8",
    )
    return results


def _extract_group_embeddings(
    groups: list[ManifestSampleGroup],
    recipe: Recipe,
    model_key: str,
    model_config: ModelConfig,
) -> list[dict]:
    extractor = EmbeddingExtractor()
    results: list[dict] = []
    for group in groups:
        try:
            feature_group = build_feature_group_from_manifest_group(group, recipe, model_key=model_key)
            embedding = extractor.extract(feature_group, model_config)
        except Exception as exc:
            raise EmbeddingExtractionError(f"{group.group_id}: embedding 提取失败: {exc}") from exc
        results.append(
            {
                "sample_id": group.sample_id,
                "group_id": group.group_id,
                "source_trace_dir": group.source_trace_dir,
                "recipe_id": recipe.recipe_id,
                "model_key": model_key,
                "camera_id": group.camera_id,
                "roi_name": group.roi_name,
                "split": group.split,
                "label_status": group.label_status,
                "lights": list(group.lights),
                "embedding": list(embedding.values),
                "embedding_dim": len(embedding.values),
                "backend": embedding.backend,
                "embedding_version": embedding.version,
                "layer_names": list(embedding.layer_names),
                "input_shape_nchw": list(embedding.input_shape_nchw) if embedding.input_shape_nchw is not None else None,
            }
        )
    return results


def _embedding_model_config(
    base: ModelConfig,
    *,
    backend: str,
    model_path: str | None,
    embedding_dim: int | None,
    channel_order: tuple[str, ...],
) -> ModelConfig:
    if backend not in {"statistical", "onnx_wideresnet50"}:
        raise EmbeddingExtractionError(f"不支持的 embedding backend: {backend}")
    if backend == "onnx_wideresnet50" and not (model_path or base.embedding_model_path):
        raise EmbeddingExtractionError("onnx_wideresnet50 backend 必须配置 model_path")
    return ModelConfig(
        backend=base.backend,
        model_path=base.model_path,
        fake_mode=base.fake_mode,
        model_family=base.model_family,
        role=base.role,
        input_channels=channel_order or base.input_channels,
        input_scale=base.input_scale,
        class_names=base.class_names,
        output_decode=base.output_decode,
        bbox_format=base.bbox_format,
        score_threshold=base.score_threshold,
        embedding_backend=backend,
        embedding_model_path=model_path or base.embedding_model_path,
        embedding_version=base.embedding_version if base.embedding_version != "none" else f"{backend}_manifest",
        embedding_dim=embedding_dim or base.embedding_dim,
        embedding_layers=base.embedding_layers,
        pca_path=base.pca_path,
        pca_version=base.pca_version,
        memory_bank_path=base.memory_bank_path,
        faiss_index_path=base.faiss_index_path,
        coreset_ratio=base.coreset_ratio,
        knn_k=base.knn_k,
        anomaly_score_scale=base.anomaly_score_scale,
    )


def _default_embedding_model_key(recipe: Recipe) -> str:
    for key, config in recipe.models.items():
        if config.embedding_backend != "none":
            return key
    for key, config in recipe.models.items():
        if config.model_family == "patchcore":
            return key
    raise TrainingDataError(f"配方没有可用于 embedding 的模型配置: {recipe.recipe_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 OK 样本多光源图批量提取 embedding")
    parser.add_argument("--manifest", required=True, type=Path, help="dataset_manifest.jsonl 路径")
    parser.add_argument("--recipe", default="seat_a_black_leather_v1", help="配方 ID")
    parser.add_argument("--model-key", default=None, help="用于 embedding 配置的模型 key，默认取配方中第一个 embedding 模型")
    parser.add_argument("--model", default=None, help="ONNX embedding 模型路径")
    parser.add_argument("--output", required=True, type=Path, help="输出 JSONL 文件路径")
    parser.add_argument("--backend", default="statistical", choices=["statistical", "onnx_wideresnet50"])
    parser.add_argument("--embedding-dim", type=int, default=None)
    parser.add_argument("--split", default=None, help="只提取指定 split 的 OK 样本")
    parser.add_argument("--channel-order", default="ch0_diffuse,ch1_polar_diffuse,ch2_high_left,ch3_high_right,ch4_high_max_min")
    args = parser.parse_args(argv)

    channel_order: tuple[str, ...] = tuple(ch.strip() for ch in args.channel_order.split(",") if ch.strip())
    try:
        results = extract_embeddings(
            manifest_path=args.manifest,
            output=args.output,
            recipe_id=args.recipe,
            model_key=args.model_key,
            embedding_dim=args.embedding_dim,
            backend=args.backend,
            model_path=args.model,
            channel_order=channel_order,
            split=args.split,
        )
    except (TrainingDataError, EmbeddingExtractionError) as exc:
        print(f"extract_embeddings_failed={exc}")
        return 2

    print(f"embeddings={args.output} samples={len(results)} backend={args.backend} recipe={args.recipe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
