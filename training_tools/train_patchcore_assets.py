from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from training_tools.build_faiss_index import build_faiss_index
from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.compute_pca import compute_pca
from training_tools.extract_embeddings import extract_embeddings_to_npy
from python_detector.config.recipe_schema import RecipeManager
from training_tools.training_errors import DimensionMismatchError, EmbeddingExtractionError, TrainingDataError


def train_patchcore_assets(
    manifest_path: Path,
    output_dir: Path,
    *,
    recipe_id: str = "seat_a_black_leather_v1",
    model_key: str | None = None,
    embedding_backend: str = "statistical",
    embedding_model_path: str | None = None,
    embedding_dim: int | None = None,
    channel_order: tuple[str, ...] | None = None,
    split: str | None = "train",
    spatial_mode: bool = False,
    spatial_layers: tuple[str, ...] = (),
    spatial_upsample_height: int = 32,
    spatial_upsample_width: int = 32,
    pca_components: int | None = None,
    pca_version: str = "pca_seat_v1",
    bank_version: str = "bank_v1",
    coreset_ratio: float = 0.1,
    coreset_method: str = "greedy",
    build_faiss: bool = False,
    faiss_index_type: str = "FlatL2",
    faiss_nlist: int | None = None,
    keep_intermediate_embeddings: bool = False,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    embeddings_npy_path = output_dir / "embeddings.npy"
    pca_path = output_dir / "seat_pca.json"
    pca_embeddings_npy_path = output_dir / "pca_embeddings.npy"
    bank_path = output_dir / "seat_patchcore_bank.json"
    bank_vectors_path = output_dir / "seat_patchcore_bank.npy"
    faiss_path = output_dir / "seat_patchcore.faiss"

    recipe = RecipeManager().load(recipe_id)
    selected_model_key = model_key or _default_embedding_model_key(recipe)
    base_model = recipe.models[selected_model_key]
    effective_input_channels = channel_order if channel_order is not None else base_model.input_channels
    effective_spatial_layers = spatial_layers if spatial_layers else base_model.spatial_layers
    effective_embedding_model_path = embedding_model_path or base_model.embedding_model_path
    training_contract = {
        "recipe_id": recipe_id,
        "model_key": selected_model_key,
        "split": split,
        "input_channels": list(effective_input_channels),
        "embedding_backend": embedding_backend,
        "embedding_model_path": effective_embedding_model_path,
        "embedding_model_sha256": _sha256_file(Path(effective_embedding_model_path)) if effective_embedding_model_path else None,
        "embedding_version": base_model.embedding_version,
        "spatial_mode": bool(spatial_mode),
        "spatial_layers": list(effective_spatial_layers),
        "spatial_upsample_height": int(spatial_upsample_height),
        "spatial_upsample_width": int(spatial_upsample_width),
        "pca_version": pca_version if pca_components is not None else None,
        "pca_components": pca_components,
        "manifest_path": str(manifest_path),
        "manifest_sha256": _sha256_file(manifest_path),
    }

    embedding_summary = extract_embeddings_to_npy(
        manifest_path=manifest_path,
        output=embeddings_npy_path,
        recipe_id=recipe_id,
        model_key=selected_model_key,
        embedding_dim=embedding_dim,
        backend=embedding_backend,
        model_path=embedding_model_path,
        channel_order=channel_order,
        split=split,
        spatial_mode=spatial_mode,
        spatial_layers=spatial_layers,
        spatial_upsample_height=spatial_upsample_height,
        spatial_upsample_width=spatial_upsample_width,
    )
    source_embeddings_path = embeddings_npy_path
    pca_result = None
    if pca_components is not None:
        pca_result = compute_pca(
            input_path=source_embeddings_path,
            output_path=pca_path,
            n_components=pca_components,
            version=pca_version,
            output_embeddings=pca_embeddings_npy_path,
        )
        source_embeddings_path = pca_embeddings_npy_path

    bank = build_memory_bank(
        source_embeddings_path,
        bank_path,
        version=bank_version,
        coreset_ratio=coreset_ratio,
        pca_version=pca_version if pca_components is not None else None,
        faiss_enabled=build_faiss,
        coreset_method=coreset_method,
        metadata=training_contract,
        vectors_path=bank_vectors_path,
    )
    faiss_result = None
    if build_faiss:
        faiss_result = build_faiss_index(
            bank_path=bank_path,
            output=faiss_path,
            index_type=faiss_index_type,
            nlist=faiss_nlist,
        )

    retained_embeddings_path = embeddings_npy_path if keep_intermediate_embeddings else None
    retained_pca_embeddings_path = pca_embeddings_npy_path if keep_intermediate_embeddings and pca_result is not None else None
    if not keep_intermediate_embeddings:
        _unlink_if_exists(embeddings_npy_path)
        _unlink_if_exists(pca_embeddings_npy_path)

    summary = {
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "recipe_id": recipe_id,
        "model_key": selected_model_key,
        "split": split,
        "embedding_backend": embedding_backend,
        "embedding_count": embedding_summary["embedding_count"],
        "embedding_dim": embedding_summary["embedding_dim"],
        "input_shape_summary": embedding_summary["input_shape_summary"],
        "training_contract": training_contract,
        "embeddings_npy_path": str(retained_embeddings_path) if retained_embeddings_path is not None else None,
        "pca_path": str(pca_path) if pca_result is not None else None,
        "pca_embeddings_npy_path": str(retained_pca_embeddings_path) if retained_pca_embeddings_path is not None else None,
        "pca_output_dim": pca_result["output_dim"] if pca_result is not None else None,
        "memory_bank_path": str(bank_path),
        "memory_bank_vectors_path": str(bank_vectors_path),
        "memory_bank_version": bank["version"],
        "memory_bank_vectors": bank["vector_count"],
        "memory_bank_dim": bank["embedding_dim"],
        "coreset_ratio": coreset_ratio,
        "coreset_method": coreset_method,
        "intermediate_embeddings_retained": keep_intermediate_embeddings,
        "faiss_index_path": str(faiss_path) if faiss_result is not None else None,
        "faiss_index_type": faiss_result["index_type"] if faiss_result is not None else None,
    }
    (output_dir / "patchcore_training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _unlink_if_exists(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return


def _default_embedding_model_key(recipe) -> str:
    for key, config in recipe.models.items():
        if config.embedding_backend != "none":
            return key
    for key, config in recipe.models.items():
        if config.model_family == "patchcore":
            return key
    raise TrainingDataError(f"配方没有可用于 embedding 的模型配置: {recipe.recipe_id}")


def _input_shape_summary(embeddings: list[dict]) -> dict:
    counts: dict[tuple[int, int, int, int] | None, int] = {}
    for entry in embeddings:
        raw_shape = entry.get("input_shape_nchw")
        shape = tuple(int(value) for value in raw_shape) if isinstance(raw_shape, list) and len(raw_shape) == 4 else None
        counts[shape] = counts.get(shape, 0) + 1
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 manifest 训练/准备 PatchCore PCA、memory bank 和可选 FAISS 资产")
    parser.add_argument("--manifest", required=True, type=Path, help="dataset_manifest.jsonl 路径")
    parser.add_argument("--output-dir", required=True, type=Path, help="资产输出目录，例如 model/patchcore")
    parser.add_argument("--recipe", default="seat_a_black_leather_v1", help="配方 ID")
    parser.add_argument("--model-key", default=None, help="PatchCore/embedding 模型 key，默认从配方自动选择")
    parser.add_argument("--embedding-backend", default="statistical", choices=["statistical", "onnx_wideresnet50"])
    parser.add_argument("--embedding-model", default=None, help="onnx_wideresnet50 模型路径")
    parser.add_argument("--embedding-dim", type=int, default=None)
    parser.add_argument("--channel-order", default=None, help="覆盖模型输入通道；默认使用配方 models.<key>.input_channels")
    parser.add_argument("--split", default="train", help="用于训练的 split；传空字符串表示不过滤")
    parser.add_argument("--spatial-mode", action="store_true", help="使用空间 PatchCore 模式（每图提取 H×W 个 patch embedding）")
    parser.add_argument("--spatial-layers", default="layer2,layer3", help="空间模式下导出的中间层，逗号分隔")
    parser.add_argument("--spatial-upsample-height", type=int, default=32)
    parser.add_argument("--spatial-upsample-width", type=int, default=32)
    parser.add_argument("--pca-components", type=int, default=None, help="启用 PCA 并指定目标维度")
    parser.add_argument("--pca-version", default="pca_seat_v1")
    parser.add_argument("--bank-version", default="bank_v1")
    parser.add_argument("--coreset-ratio", type=float, default=0.1)
    parser.add_argument("--coreset-method", default="greedy", choices=["greedy", "stride"])
    parser.add_argument("--build-faiss", action="store_true")
    parser.add_argument("--faiss-index-type", default="FlatL2", choices=["FlatL2", "IVFFlat"])
    parser.add_argument("--faiss-nlist", type=int, default=None)
    parser.add_argument(
        "--keep-intermediate-embeddings",
        action="store_true",
        help="调试时保留 embeddings.npy/pca_embeddings.npy；默认训练完成后清理中间矩阵",
    )
    args = parser.parse_args(argv)

    channel_order = None
    if args.channel_order is not None:
        channel_order = tuple(ch.strip() for ch in args.channel_order.split(",") if ch.strip())
    split = args.split if args.split else None
    spatial_layers: tuple[str, ...] = ()
    if args.spatial_mode:
        spatial_layers = tuple(layer.strip() for layer in args.spatial_layers.split(",") if layer.strip())
    try:
        summary = train_patchcore_assets(
            manifest_path=args.manifest,
            output_dir=args.output_dir,
            recipe_id=args.recipe,
            model_key=args.model_key,
            embedding_backend=args.embedding_backend,
            embedding_model_path=args.embedding_model,
            embedding_dim=args.embedding_dim,
            channel_order=channel_order,
            split=split,
            spatial_mode=args.spatial_mode,
            spatial_layers=spatial_layers,
            spatial_upsample_height=args.spatial_upsample_height,
            spatial_upsample_width=args.spatial_upsample_width,
            pca_components=args.pca_components,
            pca_version=args.pca_version,
            bank_version=args.bank_version,
            coreset_ratio=args.coreset_ratio,
            coreset_method=args.coreset_method,
            build_faiss=args.build_faiss,
            faiss_index_type=args.faiss_index_type,
            faiss_nlist=args.faiss_nlist,
            keep_intermediate_embeddings=args.keep_intermediate_embeddings,
        )
    except (TrainingDataError, DimensionMismatchError, EmbeddingExtractionError, ValueError) as exc:
        print(f"train_patchcore_assets_failed={exc}")
        return 2

    print(
        f"patchcore_assets={args.output_dir} embeddings={summary['embedding_count']} "
        f"bank_vectors={summary['memory_bank_vectors']} faiss={summary['faiss_index_path'] or 'disabled'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
