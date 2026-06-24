from __future__ import annotations

import argparse
import json
from pathlib import Path

from training_tools.build_faiss_index import build_faiss_index
from training_tools.build_patchcore_memory_bank import build_memory_bank
from training_tools.compute_pca import compute_pca
from training_tools.extract_embeddings import extract_embeddings
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
    pca_components: int | None = None,
    pca_version: str = "pca_seat_v1",
    bank_version: str = "bank_v1",
    coreset_ratio: float = 0.1,
    coreset_method: str = "greedy",
    build_faiss: bool = False,
    faiss_index_type: str = "FlatL2",
    faiss_nlist: int | None = None,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    embeddings_path = output_dir / "embeddings.jsonl"
    pca_path = output_dir / "seat_pca.json"
    pca_embeddings_path = output_dir / "pca_embeddings.jsonl"
    bank_path = output_dir / "seat_patchcore_bank.json"
    faiss_path = output_dir / "seat_patchcore.faiss"

    embeddings = extract_embeddings(
        manifest_path=manifest_path,
        output=embeddings_path,
        recipe_id=recipe_id,
        model_key=model_key,
        embedding_dim=embedding_dim,
        backend=embedding_backend,
        model_path=embedding_model_path,
        channel_order=channel_order,
        split=split,
    )
    source_embeddings_path = embeddings_path
    pca_result = None
    if pca_components is not None:
        pca_result = compute_pca(
            input_path=embeddings_path,
            output_path=pca_path,
            n_components=pca_components,
            version=pca_version,
            output_embeddings=pca_embeddings_path,
        )
        source_embeddings_path = pca_embeddings_path

    bank = build_memory_bank(
        source_embeddings_path,
        bank_path,
        version=bank_version,
        coreset_ratio=coreset_ratio,
        pca_version=pca_version if pca_components is not None else None,
        faiss_enabled=build_faiss,
        coreset_method=coreset_method,
    )
    faiss_result = None
    if build_faiss:
        faiss_result = build_faiss_index(
            bank_path=bank_path,
            output=faiss_path,
            index_type=faiss_index_type,
            nlist=faiss_nlist,
        )

    summary = {
        "manifest": str(manifest_path),
        "output_dir": str(output_dir),
        "recipe_id": recipe_id,
        "model_key": model_key,
        "split": split,
        "embedding_backend": embedding_backend,
        "embedding_count": len(embeddings),
        "embedding_dim": len(embeddings[0]["embedding"]) if embeddings else 0,
        "embeddings_path": str(embeddings_path),
        "pca_path": str(pca_path) if pca_result is not None else None,
        "pca_embeddings_path": str(pca_embeddings_path) if pca_result is not None else None,
        "pca_output_dim": pca_result["output_dim"] if pca_result is not None else None,
        "memory_bank_path": str(bank_path),
        "memory_bank_version": bank["version"],
        "memory_bank_vectors": len(bank["vectors"]),
        "memory_bank_dim": bank["embedding_dim"],
        "coreset_ratio": coreset_ratio,
        "coreset_method": coreset_method,
        "faiss_index_path": str(faiss_path) if faiss_result is not None else None,
        "faiss_index_type": faiss_result["index_type"] if faiss_result is not None else None,
    }
    (output_dir / "patchcore_training_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


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
    parser.add_argument("--pca-components", type=int, default=None, help="启用 PCA 并指定目标维度")
    parser.add_argument("--pca-version", default="pca_seat_v1")
    parser.add_argument("--bank-version", default="bank_v1")
    parser.add_argument("--coreset-ratio", type=float, default=0.1)
    parser.add_argument("--coreset-method", default="greedy", choices=["greedy", "stride"])
    parser.add_argument("--build-faiss", action="store_true")
    parser.add_argument("--faiss-index-type", default="FlatL2", choices=["FlatL2", "IVFFlat"])
    parser.add_argument("--faiss-nlist", type=int, default=None)
    args = parser.parse_args(argv)

    channel_order = None
    if args.channel_order is not None:
        channel_order = tuple(ch.strip() for ch in args.channel_order.split(",") if ch.strip())
    split = args.split if args.split else None
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
            pca_components=args.pca_components,
            pca_version=args.pca_version,
            bank_version=args.bank_version,
            coreset_ratio=args.coreset_ratio,
            coreset_method=args.coreset_method,
            build_faiss=args.build_faiss,
            faiss_index_type=args.faiss_index_type,
            faiss_nlist=args.faiss_nlist,
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
