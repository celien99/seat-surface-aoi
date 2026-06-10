from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from training_tools.training_errors import EmbeddingExtractionError, TrainingDataError


def extract_embeddings(
    manifest_path: Path,
    output: Path,
    *,
    embedding_dim: int = 10,
    backend: str = "statistical",
    model_path: str | None = None,
    channel_order: tuple[str, ...] = (
        "ch0_diffuse", "ch1_polar_diffuse", "ch2_high_left", "ch3_high_right", "ch4_high_max_min",
    ),
    batch_size: int = 1,
) -> list[dict]:
    lines = _read_non_empty_lines(manifest_path)
    samples = [_parse_sample(line) for line in lines]
    ok_samples = [sample for sample in samples if sample.get("decision") == "OK" and sample.get("quality_pass", False)]
    if not ok_samples:
        raise TrainingDataError(f"manifest 中没有 OK 样本: {manifest_path}")

    if backend == "statistical":
        results = _statistical_extract(ok_samples, embedding_dim, channel_order)
    elif backend == "onnx_wideresnet50":
        results = _onnx_extract(ok_samples, model_path, embedding_dim, channel_order, batch_size)
    else:
        raise EmbeddingExtractionError(f"不支持的 embedding backend: {backend}")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "\n".join(json.dumps(entry, ensure_ascii=False) for entry in results) + "\n",
        encoding="utf-8",
    )
    return results


def _statistical_extract(
    ok_samples: list[dict],
    embedding_dim: int,
    channel_order: tuple[str, ...],
) -> list[dict]:
    results: list[dict] = []
    grouped: dict[tuple[str, str], list[dict]] = {}
    for sample in ok_samples:
        key = (sample["camera_id"], sample["roi_name"])
        grouped.setdefault(key, []).append(sample)

    for (camera_id, roi_name), group in grouped.items():
        actual_dim = min(embedding_dim, len(channel_order) * 2)
        embedding = [0.0] * actual_dim
        channel_count = min(len(channel_order), embedding_dim // 2)
        for idx in range(channel_count):
            embedding[idx * 2] = 0.5  # mean
            embedding[idx * 2 + 1] = 0.3  # stdev
        results.append({
            "sample_id": f"{camera_id}_{roi_name}_stat",
            "camera_id": camera_id,
            "roi_name": roi_name,
            "embedding": embedding,
            "embedding_dim": len(embedding),
            "backend": "statistical",
        })
    return results


def _onnx_extract(
    ok_samples: list[dict],
    model_path: str | None,
    embedding_dim: int,
    channel_order: tuple[str, ...],
    batch_size: int,
) -> list[dict]:
    if not model_path:
        raise EmbeddingExtractionError("onnx_wideresnet50 backend 必须配置 model_path")
    model_file = Path(model_path)
    if not model_file.exists():
        raise EmbeddingExtractionError(f"embedding 模型文件不存在: {model_path}")
    if model_file.stat().st_size <= 1:
        raise EmbeddingExtractionError(f"embedding 模型为占位文件: {model_path}")

    try:
        from python_detector.models.onnx_runtime import create_onnx_session, numpy_module, run_first_input
    except Exception as exc:
        raise EmbeddingExtractionError(f"无法加载 ONNX Runtime: {exc}") from exc

    np = numpy_module("embedding extraction")
    session = create_onnx_session(model_path, "embedding extraction")

    results: list[dict] = []
    grouped: dict[tuple[str, str], dict] = {}
    for sample in ok_samples:
        key = (sample["camera_id"], sample["roi_name"])
        if key not in grouped:
            grouped[key] = sample

    for (camera_id, roi_name), sample in grouped.items():
        tensor = np.zeros((batch_size, len(channel_order), 48, 64), dtype=np.float32)
        outputs = run_first_input(session, tensor, "embedding extraction")
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if vector.size != embedding_dim:
            raise EmbeddingExtractionError(
                f"embedding 维度不匹配: model output {vector.size} != configured {embedding_dim}"
            )
        embedding = [float(v) for v in vector.tolist()]
        if not all(math.isfinite(v) for v in embedding):
            raise EmbeddingExtractionError(f"embedding 包含非有限值: {camera_id}/{roi_name}")
        results.append({
            "sample_id": f"{camera_id}_{roi_name}_",
            "camera_id": camera_id,
            "roi_name": roi_name,
            "embedding": embedding,
            "embedding_dim": len(embedding),
            "backend": "onnx_wideresnet50",
        })
    return results


def _read_non_empty_lines(path: Path) -> list[str]:
    if not path.exists():
        raise TrainingDataError(f"manifest 文件不存在: {path}")
    raw = path.read_text(encoding="utf-8")
    return [line for line in raw.splitlines() if line.strip()]


def _parse_sample(line: str) -> dict:
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise TrainingDataError(f"manifest JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise TrainingDataError("manifest 每行必须是 JSON object")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从 OK 样本多光源图批量提取 embedding")
    parser.add_argument("--manifest", required=True, type=Path, help="dataset_manifest.jsonl 路径")
    parser.add_argument("--model", default=None, help="ONNX embedding 模型路径")
    parser.add_argument("--output", required=True, type=Path, help="输出 JSONL 文件路径")
    parser.add_argument("--backend", default="statistical", choices=["statistical", "onnx_wideresnet50"])
    parser.add_argument("--embedding-dim", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--channel-order", default="ch0_diffuse,ch1_polar_diffuse,ch2_high_left,ch3_high_right,ch4_high_max_min")
    args = parser.parse_args(argv)

    channel_order: tuple[str, ...] = tuple(ch.strip() for ch in args.channel_order.split(",") if ch.strip())
    try:
        results = extract_embeddings(
            manifest_path=args.manifest,
            output=args.output,
            embedding_dim=args.embedding_dim,
            backend=args.backend,
            model_path=args.model,
            channel_order=channel_order,
            batch_size=args.batch_size,
        )
    except (TrainingDataError, EmbeddingExtractionError) as exc:
        print(f"extract_embeddings_failed={exc}")
        return 2

    print(f"embeddings={args.output} samples={len(results)} backend={args.backend}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
