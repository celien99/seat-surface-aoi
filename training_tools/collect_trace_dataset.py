from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class TraceDatasetError(RuntimeError):
    """trace 转训练样本失败。"""


_IMAGE_SUFFIXES = {".png"}


@dataclass(frozen=True)
class DatasetSample:
    sample_id: str
    source_trace_dir: str
    recipe_id: str
    seat_id: str
    sequence_id: int
    decision: str
    quality_pass: bool
    camera_id: str
    pose_id: str
    roi_name: str
    light_id: str
    image_path: str
    has_defect: bool
    bbox_xyxy_pixel: list[list[int]]
    split: str
    label_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "source_trace_dir": self.source_trace_dir,
            "recipe_id": self.recipe_id,
            "seat_id": self.seat_id,
            "sequence_id": self.sequence_id,
            "decision": self.decision,
            "quality_pass": self.quality_pass,
            "camera_id": self.camera_id,
            "pose_id": self.pose_id,
            "roi_name": self.roi_name,
            "light_id": self.light_id,
            "image_path": self.image_path,
            "has_defect": self.has_defect,
            "bbox_xyxy_pixel": self.bbox_xyxy_pixel,
            "split": self.split,
            "label_status": self.label_status,
        }


def collect_trace_dataset(
    trace_roots: list[Path],
    output_dir: Path,
    *,
    split: str = "unassigned",
    label_status: str = "unlabeled",
    write_summary: bool = True,
    filter_decision: str | None = None,
) -> list[DatasetSample]:
    trace_dirs = _discover_trace_dirs(trace_roots)
    if not trace_dirs:
        raise TraceDatasetError(f"没有发现可用 trace 记录: {', '.join(str(path) for path in trace_roots)}")

    samples: list[DatasetSample] = []
    for trace_dir in trace_dirs:
        samples.extend(
            _collect_trace_dir(
                trace_dir,
                output_dir,
                split=split,
                label_status=label_status,
            )
        )
    if filter_decision is not None:
        samples = [sample for sample in samples if sample.decision == filter_decision]
    if not samples:
        raise TraceDatasetError("trace 中没有发现 ROI 图像样本")

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "dataset_manifest.jsonl"
    manifest_path.write_text(
        "\n".join(json.dumps(sample.as_dict(), ensure_ascii=False, sort_keys=True) for sample in samples) + "\n",
        encoding="utf-8",
    )
    if write_summary:
        (output_dir / "dataset_summary.json").write_text(
            json.dumps(_summary(samples), ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return samples


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从检测 trace 目录生成离线训练样本 manifest 和 ROI 图像副本")
    parser.add_argument("--trace-root", action="append", required=True, type=Path, help="trace 根目录或单条 trace 目录，可重复传入")
    parser.add_argument("--output", required=True, type=Path, help="输出数据集目录")
    parser.add_argument("--split", default="unassigned", help="写入 manifest 的 split 字段")
    parser.add_argument("--label-status", default="unlabeled", help="写入 manifest 的 label_status 字段")
    parser.add_argument("--filter-decision", default=None, help="仅收集指定决策的样本，例如 OK、NG")
    parser.add_argument("--no-summary", action="store_true", help="不生成 dataset_summary.json")
    args = parser.parse_args(argv)

    try:
        samples = collect_trace_dataset(
            args.trace_root,
            args.output,
            split=args.split,
            label_status=args.label_status,
            write_summary=not args.no_summary,
            filter_decision=args.filter_decision,
        )
    except TraceDatasetError as exc:
        print(f"collect_trace_dataset_failed={exc}")
        return 2

    print(
        f"dataset={args.output} manifest={args.output / 'dataset_manifest.jsonl'} "
        f"samples={len(samples)} traces={len({sample.source_trace_dir for sample in samples})}"
    )
    return 0


def _discover_trace_dirs(trace_roots: list[Path]) -> list[Path]:
    trace_dirs: list[Path] = []
    for root in trace_roots:
        if not root.exists():
            raise TraceDatasetError(f"trace 路径不存在: {root}")
        if _is_trace_dir(root):
            trace_dirs.append(root)
            continue
        trace_dirs.extend(sorted(path.parent for path in root.rglob("result.json") if _is_trace_dir(path.parent)))
    return sorted(set(trace_dirs))


def _is_trace_dir(path: Path) -> bool:
    return (path / "result.json").is_file()


def _collect_trace_dir(
    trace_dir: Path,
    output_dir: Path,
    *,
    split: str,
    label_status: str,
) -> list[DatasetSample]:
    result = _load_json(trace_dir / "result.json")
    if not isinstance(result, dict):
        raise TraceDatasetError(f"result.json 必须是 JSON object: {trace_dir}")
    job = _load_optional_json(trace_dir / "job.json")
    recipe_summary = _load_optional_json(trace_dir / "recipe_summary.json")

    images_dir = trace_dir / "images"
    if not images_dir.is_dir():
        raise TraceDatasetError(f"trace 缺少 ROI 图像目录: {images_dir}")

    sequence_id = _int_value(result.get("sequence_id", _dict_get(job, "sequence_id", 0)), "sequence_id", trace_dir)
    seat_id = str(result.get("seat_id") or _dict_get(job, "seat_id", "") or "")
    decision = str(result.get("decision") or "")
    if not decision:
        raise TraceDatasetError(f"result.json 缺少 decision: {trace_dir}")
    quality_pass = bool(result.get("quality_pass", False))
    recipe_id = str(_dict_get(recipe_summary, "recipe_id", _dict_get(job, "recipe_id", "")) or "")
    defects = _defects_by_roi(result)

    samples: list[DatasetSample] = []
    for image_path, camera_id, pose_id, roi_name, light_id in _iter_trace_images(images_dir):
        defect_items = defects.get((camera_id, pose_id, roi_name), [])
        sample_id = _sample_id(trace_dir, sequence_id, camera_id, pose_id, roi_name, light_id)
        relative_image_path = Path("images") / camera_id / pose_id / roi_name / light_id / f"{sample_id}{image_path.suffix.lower()}"
        destination = output_dir / relative_image_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(image_path, destination)
        samples.append(
            DatasetSample(
                sample_id=sample_id,
                source_trace_dir=str(trace_dir),
                recipe_id=recipe_id,
                seat_id=seat_id,
                sequence_id=sequence_id,
                decision=decision,
                quality_pass=quality_pass,
                camera_id=camera_id,
                pose_id=pose_id,
                roi_name=roi_name,
                light_id=light_id,
                image_path=relative_image_path.as_posix(),
                has_defect=bool(defect_items),
                bbox_xyxy_pixel=[_bbox(item, trace_dir) for item in defect_items],
                split=split,
                label_status=label_status,
            )
        )
    if not samples:
        raise TraceDatasetError(f"trace 没有 ROI 图像: {trace_dir}")
    return samples


def _load_json(path: Path) -> Any:
    if not path.is_file():
        raise TraceDatasetError(f"缺少必需文件: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TraceDatasetError(f"JSON 解析失败: {path}: {exc}") from exc


def _load_optional_json(path: Path) -> Any:
    if not path.is_file():
        return {}
    return _load_json(path)


def _dict_get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, dict) else default


def _int_value(value: Any, name: str, trace_dir: Path) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TraceDatasetError(f"{name} 必须是整数: {trace_dir}") from exc


def _iter_trace_images(images_dir: Path) -> list[tuple[Path, str, str, str, str]]:
    images: list[tuple[Path, str, str, str, str]] = []
    for image_path in sorted(path for path in images_dir.glob("*/*/*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES):
        camera_id = image_path.parent.parent.name
        pose_id = camera_id
        roi_name = image_path.parent.name
        light_id = image_path.stem
        images.append((image_path, camera_id, pose_id, roi_name, light_id))
    for image_path in sorted(path for path in images_dir.glob("*/*/*/*") if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES):
        camera_id = image_path.parent.parent.parent.name
        pose_id = image_path.parent.parent.name
        roi_name = image_path.parent.name
        light_id = image_path.stem
        images.append((image_path, camera_id, pose_id, roi_name, light_id))
    return images


def _defects_by_roi(result: dict[str, Any]) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    defects: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    raw_defects = result.get("defects", [])
    if not isinstance(raw_defects, list):
        return defects
    for item in raw_defects:
        if not isinstance(item, dict):
            continue
        camera_id = str(item.get("camera_id") or "")
        pose_id = str(item.get("pose_id") or camera_id)
        roi_name = str(item.get("roi_name") or "")
        if not camera_id or not roi_name:
            continue
        defects.setdefault((camera_id, pose_id, roi_name), []).append(item)
    return defects


def _bbox(defect: dict[str, Any], trace_dir: Path) -> list[int]:
    raw_bbox = defect.get("bbox_xyxy_pixel", [])
    if (
        not isinstance(raw_bbox, (list, tuple))
        or len(raw_bbox) != 4
        or not all(isinstance(value, (int, float)) for value in raw_bbox)
    ):
        raise TraceDatasetError(f"defect bbox_xyxy_pixel 无效: {trace_dir}")
    return [int(value) for value in raw_bbox]


def _sample_id(trace_dir: Path, sequence_id: int, camera_id: str, pose_id: str, roi_name: str, light_id: str) -> str:
    safe_trace = _safe_name(trace_dir.name)
    trace_hash = hashlib.sha1(str(trace_dir.resolve()).encode("utf-8")).hexdigest()[:8]
    return "_".join(
        _safe_name(part)
        for part in (
            safe_trace,
            trace_hash,
            str(sequence_id),
            camera_id,
            pose_id,
            roi_name,
            light_id,
        )
    )


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))


def _summary(samples: list[DatasetSample]) -> dict[str, Any]:
    decisions: dict[str, int] = {}
    cameras: dict[str, int] = {}
    for sample in samples:
        decisions[sample.decision] = decisions.get(sample.decision, 0) + 1
        cameras[sample.camera_id] = cameras.get(sample.camera_id, 0) + 1
    return {
        "sample_count": len(samples),
        "trace_count": len({sample.source_trace_dir for sample in samples}),
        "decision_counts": decisions,
        "camera_counts": cameras,
        "defect_sample_count": sum(1 for sample in samples if sample.has_defect),
        "label_status_counts": {status: sum(1 for sample in samples if sample.label_status == status) for status in sorted({sample.label_status for sample in samples})},
    }


if __name__ == "__main__":
    raise SystemExit(main())
