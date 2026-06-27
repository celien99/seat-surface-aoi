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
    channel_order: tuple[str, ...] | None = None,
    split: str | None = None,
    spatial_mode: bool = False,
    spatial_layers: tuple[str, ...] = (),
    spatial_upsample_height: int = 32,
    spatial_upsample_width: int = 32,
) -> list[dict]:
    result = extract_embeddings_to_file(
        manifest_path=manifest_path,
        output=output,
        recipe_id=recipe_id,
        model_key=model_key,
        embedding_dim=embedding_dim,
        backend=backend,
        model_path=model_path,
        channel_order=channel_order,
        split=split,
        spatial_mode=spatial_mode,
        spatial_layers=spatial_layers,
        spatial_upsample_height=spatial_upsample_height,
        spatial_upsample_width=spatial_upsample_width,
        collect_results=True,
    )
    return result["results"]


def extract_embeddings_to_file(
    manifest_path: Path,
    output: Path,
    *,
    recipe_id: str = "seat_a_black_leather_v1",
    model_key: str | None = None,
    embedding_dim: int | None = None,
    backend: str = "statistical",
    model_path: str | None = None,
    channel_order: tuple[str, ...] | None = None,
    split: str | None = None,
    spatial_mode: bool = False,
    spatial_layers: tuple[str, ...] = (),
    spatial_upsample_height: int = 32,
    spatial_upsample_width: int = 32,
    collect_results: bool = False,
) -> dict:
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
        spatial_mode=spatial_mode,
        spatial_layers=spatial_layers,
        spatial_upsample_height=spatial_upsample_height,
        spatial_upsample_width=spatial_upsample_width,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = _write_group_embeddings(
        groups,
        recipe,
        selected_model_key,
        model_config,
        output,
        collect_results=collect_results,
    )
    return summary


def extract_embeddings_to_npy(
    manifest_path: Path,
    output: Path,
    *,
    recipe_id: str = "seat_a_black_leather_v1",
    model_key: str | None = None,
    embedding_dim: int | None = None,
    backend: str = "statistical",
    model_path: str | None = None,
    channel_order: tuple[str, ...] | None = None,
    split: str | None = None,
    spatial_mode: bool = False,
    spatial_layers: tuple[str, ...] = (),
    spatial_upsample_height: int = 32,
    spatial_upsample_width: int = 32,
) -> dict:
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
        spatial_mode=spatial_mode,
        spatial_layers=spatial_layers,
        spatial_upsample_height=spatial_upsample_height,
        spatial_upsample_width=spatial_upsample_width,
    )
    return _write_group_embedding_matrix(groups, recipe, selected_model_key, model_config, output)


def _extract_group_embeddings(
    groups: list[ManifestSampleGroup],
    recipe: Recipe,
    model_key: str,
    model_config: ModelConfig,
) -> list[dict]:
    return _write_group_embeddings(
        groups,
        recipe,
        model_key,
        model_config,
        output=None,
        collect_results=True,
    )["results"]


def _write_group_embedding_matrix(
    groups: list[ManifestSampleGroup],
    recipe: Recipe,
    model_key: str,
    model_config: ModelConfig,
    output: Path,
) -> dict:
    import numpy as np

    extractor = EmbeddingExtractor()
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    rows_per_group = (
        int(model_config.spatial_upsample_height) * int(model_config.spatial_upsample_width)
        if model_config.spatial_mode and model_config.spatial_layers
        else 1
    )
    total_rows = len(groups) * rows_per_group
    if total_rows <= 0:
        raise TrainingDataError(f"manifest 中没有 OK 样本: {output}")

    embedding_count = 0
    embedding_dim = 0
    input_shape_counts: dict[tuple[int, int, int, int] | None, int] = {}
    matrix = None
    try:
        for group in groups:
            try:
                feature_group = build_feature_group_from_manifest_group(group, recipe, model_key=model_key)
                if model_config.spatial_mode and model_config.spatial_layers:
                    spatial = extractor.extract_spatial(feature_group, model_config)
                    values = np.asarray(spatial.patch_embeddings, dtype=np.float32)
                    if values.ndim != 2:
                        raise RuntimeError(f"spatial embedding 必须是二维矩阵: {values.shape}")
                    if values.shape[0] != rows_per_group:
                        raise RuntimeError(f"spatial embedding patch 数不匹配: {values.shape[0]} != {rows_per_group}")
                    raw_shape = list(spatial.input_shape_nchw) if spatial.input_shape_nchw is not None else None
                else:
                    embedding = extractor.extract(feature_group, model_config)
                    values = np.asarray([embedding.values], dtype=np.float32)
                    raw_shape = list(embedding.input_shape_nchw) if embedding.input_shape_nchw is not None else None
                if values.ndim != 2 or values.shape[0] <= 0 or values.shape[1] <= 0:
                    raise RuntimeError(f"embedding 输出必须是非空二维矩阵: {values.shape}")
                if not bool(np.isfinite(values).all()):
                    raise RuntimeError("embedding 输出包含非有限值")
                if matrix is None:
                    embedding_dim = int(values.shape[1])
                    matrix = np.lib.format.open_memmap(
                        output,
                        mode="w+",
                        dtype=np.float32,
                        shape=(total_rows, embedding_dim),
                    )
                elif values.shape[1] != embedding_dim:
                    raise RuntimeError(f"embedding 维度不一致: {values.shape[1]} != {embedding_dim}")
                end = embedding_count + int(values.shape[0])
                matrix[embedding_count:end, :] = values
                embedding_count = end
                shape = tuple(int(value) for value in raw_shape) if isinstance(raw_shape, list) and len(raw_shape) == 4 else None
                input_shape_counts[shape] = input_shape_counts.get(shape, 0) + int(values.shape[0])
            except Exception as exc:
                raise EmbeddingExtractionError(f"{group.group_id}: embedding 提取失败: {exc}") from exc
        if matrix is None:
            raise TrainingDataError(f"manifest 中没有 OK 样本: {output}")
        if embedding_count != total_rows:
            raise EmbeddingExtractionError(f"embedding 写入数量不匹配: {embedding_count} != {total_rows}")
        matrix.flush()
    except Exception:
        if output.exists():
            output.unlink()
        raise

    return {
        "embedding_count": embedding_count,
        "embedding_dim": embedding_dim,
        "input_shape_summary": _input_shape_summary_from_counts(input_shape_counts),
        "matrix_path": str(output),
        "results": [],
    }


def _write_group_embeddings(
    groups: list[ManifestSampleGroup],
    recipe: Recipe,
    model_key: str,
    model_config: ModelConfig,
    output: Path | None,
    *,
    collect_results: bool,
) -> dict:
    extractor = EmbeddingExtractor()
    results: list[dict] = []
    embedding_count = 0
    embedding_dim = 0
    input_shape_counts: dict[tuple[int, int, int, int] | None, int] = {}

    handle = output.open("w", encoding="utf-8") if output is not None else None
    try:
        def emit(entry: dict) -> None:
            nonlocal embedding_count, embedding_dim
            embedding_count += 1
            embedding_dim = int(entry.get("embedding_dim") or len(entry.get("embedding", ())))
            raw_shape = entry.get("input_shape_nchw")
            shape = tuple(int(value) for value in raw_shape) if isinstance(raw_shape, list) and len(raw_shape) == 4 else None
            input_shape_counts[shape] = input_shape_counts.get(shape, 0) + 1
            if handle is not None:
                handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
            if collect_results:
                results.append(entry)

        for group in groups:
            try:
                feature_group = build_feature_group_from_manifest_group(group, recipe, model_key=model_key)
                if model_config.spatial_mode and model_config.spatial_layers:
                    spatial = extractor.extract_spatial(feature_group, model_config)
                    for patch_idx, patch_embedding in enumerate(spatial.patch_embeddings):
                        patch_row = patch_idx // spatial.spatial_shape[1]
                        patch_col = patch_idx % spatial.spatial_shape[1]
                        emit(
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
                                "embedding": [float(value) for value in patch_embedding.tolist()],
                                "embedding_dim": len(patch_embedding),
                                "backend": spatial.backend,
                                "embedding_version": spatial.version,
                                "layer_names": list(spatial.layer_names),
                                "input_shape_nchw": list(spatial.input_shape_nchw) if spatial.input_shape_nchw is not None else None,
                                "spatial_mode": True,
                                "patch_index": patch_idx,
                                "patch_row": patch_row,
                                "patch_col": patch_col,
                                "spatial_shape": list(spatial.spatial_shape),
                                "patch_dim": spatial.patch_dim,
                            }
                        )
                else:
                    embedding = extractor.extract(feature_group, model_config)
                    emit(
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
                            "embedding": [float(value) for value in embedding.values],
                            "embedding_dim": len(embedding.values),
                            "backend": embedding.backend,
                            "embedding_version": embedding.version,
                            "layer_names": list(embedding.layer_names),
                            "input_shape_nchw": list(embedding.input_shape_nchw) if embedding.input_shape_nchw is not None else None,
                        }
                    )
            except Exception as exc:
                raise EmbeddingExtractionError(f"{group.group_id}: embedding 提取失败: {exc}") from exc
    finally:
        if handle is not None:
            handle.close()

    return {
        "embedding_count": embedding_count,
        "embedding_dim": embedding_dim,
        "input_shape_summary": _input_shape_summary_from_counts(input_shape_counts),
        "results": results,
    }


def _input_shape_summary_from_counts(counts: dict[tuple[int, int, int, int] | None, int]) -> dict:
    shapes = [
        {
            "input_shape_nchw": list(shape) if shape is not None else None,
            "count": count,
        }
        for shape, count in sorted(counts.items(), key=lambda item: item[0] or (0, 0, 0, 0))
    ]
    heights = [shape[2] for shape in counts if shape is not None]
    widths = [shape[3] for shape in counts if shape is not None]
    return {
        "distinct_shapes": shapes,
        "height_min": min(heights) if heights else None,
        "height_max": max(heights) if heights else None,
        "width_min": min(widths) if widths else None,
        "width_max": max(widths) if widths else None,
        "fixed_input_size": len({(shape[2], shape[3]) for shape in counts if shape is not None}) == 1,
    }


def _embedding_model_config(
    base: ModelConfig,
    *,
    backend: str,
    model_path: str | None,
    embedding_dim: int | None,
    channel_order: tuple[str, ...] | None,
    spatial_mode: bool = False,
    spatial_layers: tuple[str, ...] = (),
    spatial_upsample_height: int = 32,
    spatial_upsample_width: int = 32,
) -> ModelConfig:
    if backend not in {"statistical", "onnx_wideresnet50"}:
        raise EmbeddingExtractionError(f"不支持的 embedding backend: {backend}")
    if backend == "onnx_wideresnet50" and not (model_path or base.embedding_model_path):
        raise EmbeddingExtractionError("onnx_wideresnet50 backend 必须配置 model_path")
    if spatial_mode and backend != "onnx_wideresnet50":
        raise EmbeddingExtractionError("spatial_mode 必须使用 onnx_wideresnet50 backend")
    return ModelConfig(
        backend=base.backend,
        model_path=base.model_path,
        fake_mode=base.fake_mode,
        model_family=base.model_family,
        role=base.role,
        input_channels=channel_order if channel_order is not None else base.input_channels,
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
        spatial_mode=spatial_mode,
        spatial_layers=spatial_layers if spatial_layers else base.spatial_layers,
        spatial_upsample_height=spatial_upsample_height,
        spatial_upsample_width=spatial_upsample_width,
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
    parser.add_argument("--channel-order", default=None, help="覆盖模型输入通道；默认使用配方 models.<key>.input_channels")
    parser.add_argument("--spatial-mode", action="store_true", help="提取空间 patch embedding（每图 H×W 个向量）")
    parser.add_argument("--spatial-layers", default="layer2,layer3", help="空间模式下导出的中间层，逗号分隔")
    parser.add_argument("--spatial-upsample-height", type=int, default=32, help="空间特征上采样目标高度")
    parser.add_argument("--spatial-upsample-width", type=int, default=32, help="空间特征上采样目标宽度")
    args = parser.parse_args(argv)

    channel_order: tuple[str, ...] | None = None
    if args.channel_order is not None:
        channel_order = tuple(ch.strip() for ch in args.channel_order.split(",") if ch.strip())
    spatial_layers: tuple[str, ...] = ()
    if args.spatial_mode:
        spatial_layers = tuple(layer.strip() for layer in args.spatial_layers.split(",") if layer.strip())
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
            spatial_mode=args.spatial_mode,
            spatial_layers=spatial_layers,
            spatial_upsample_height=args.spatial_upsample_height,
            spatial_upsample_width=args.spatial_upsample_width,
        )
    except (TrainingDataError, EmbeddingExtractionError) as exc:
        print(f"extract_embeddings_failed={exc}")
        return 2

    print(f"embeddings={args.output} samples={len(results)} backend={args.backend} recipe={args.recipe} spatial_mode={args.spatial_mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
