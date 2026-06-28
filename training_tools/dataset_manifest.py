from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from python_detector.config.recipe_schema import Recipe
from python_detector.image_codec import ImageCodecError, RasterImage, load_gray_image
from python_detector.ipc.data_types import LightFrame
from python_detector.pipeline.feature_builder import FeatureBuilder, FeatureGroup
from python_detector.pipeline.reflectance_cube import ReflectanceCube, RegistrationReport
from training_tools.training_errors import TrainingDataError


@dataclass(frozen=True)
class ManifestRow:
    line_number: int
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
    split: str
    label_status: str
    has_defect: bool
    bbox_xyxy_pixel: tuple[tuple[int, int, int, int], ...]
    ground_truth: tuple[dict[str, Any], ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ManifestSampleGroup:
    group_id: str
    manifest_path: Path
    dataset_root: Path
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
    split: str
    label_status: str
    rows: tuple[ManifestRow, ...]

    @property
    def lights(self) -> tuple[str, ...]:
        return tuple(row.light_id for row in self.rows)

    @property
    def ground_truths(self) -> tuple[dict[str, Any], ...]:
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for row in self.rows:
            for item in row.ground_truth:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if key in seen:
                    continue
                seen.add(key)
                items.append(item)
        return tuple(items)


def load_manifest_rows(manifest_path: Path) -> list[ManifestRow]:
    if not manifest_path.exists():
        raise TrainingDataError(f"manifest 不存在: {manifest_path}")
    rows: list[ManifestRow] = []
    for line_number, line in enumerate(manifest_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingDataError(f"{manifest_path}:{line_number}: JSON 解析失败: {exc}") from exc
        if not isinstance(raw, dict):
            raise TrainingDataError(f"{manifest_path}:{line_number}: 每行必须是 JSON object")
        rows.append(_row_from_dict(raw, line_number, manifest_path))
    return rows


def load_manifest_groups(manifest_path: Path) -> list[ManifestSampleGroup]:
    rows = load_manifest_rows(manifest_path)
    if not rows:
        raise TrainingDataError(f"manifest 没有样本: {manifest_path}")
    dataset_root = manifest_path.parent
    grouped: dict[tuple[str, str, str, str, str], list[ManifestRow]] = {}
    for row in rows:
        key = (_base_sample_id(row.sample_id, row.light_id), row.source_trace_dir, row.camera_id, row.pose_id, row.roi_name)
        grouped.setdefault(key, []).append(row)

    groups: list[ManifestSampleGroup] = []
    for key, group_rows in sorted(grouped.items(), key=lambda item: item[0]):
        ordered = tuple(sorted(group_rows, key=lambda row: row.light_id))
        first = ordered[0]
        _assert_group_consistent(ordered, manifest_path)
        groups.append(
            ManifestSampleGroup(
                group_id="|".join(key),
                manifest_path=manifest_path,
                dataset_root=dataset_root,
                sample_id=key[0],
                source_trace_dir=first.source_trace_dir,
                recipe_id=first.recipe_id,
                seat_id=first.seat_id,
                sequence_id=first.sequence_id,
                decision=first.decision,
                quality_pass=first.quality_pass,
                camera_id=first.camera_id,
                pose_id=first.pose_id,
                roi_name=first.roi_name,
                split=first.split,
                label_status=first.label_status,
                rows=ordered,
            )
        )
    return groups


def build_feature_group_from_manifest_group(
    group: ManifestSampleGroup,
    recipe: Recipe,
    *,
    model_key: str | None = None,
    feature_builder: FeatureBuilder | None = None,
) -> FeatureGroup:
    frames = {
        row.light_id: light_frame_from_manifest_row(row, group.dataset_root)
        for row in group.rows
    }
    if not frames:
        raise TrainingDataError(f"{group.group_id}: 没有图像帧")

    selected_model_key = model_key or recipe.model_key_for(group.camera_id, group.roi_name, group.pose_id)
    if selected_model_key not in recipe.models:
        raise TrainingDataError(f"{group.group_id}: 配方缺少模型配置: {selected_model_key}")
    light_order = tuple(light_id for light_id in recipe.light_order if light_id in frames) or tuple(frames)
    first_frame = next(iter(frames.values()))
    registration = RegistrationReport(
        camera_id=group.camera_id,
        pose_id=group.pose_id,
        roi_name=group.roi_name,
        base_light_id=light_order[0],
        calibration_id=first_frame.calibration_id,
        max_error_px=0.0,
        mean_error_px=0.0,
        method="manifest_roi",
        is_pass=True,
        message="manifest ROI images are pre-aligned trace crops",
    )
    cube = ReflectanceCube(
        sequence_id=group.sequence_id,
        trigger_id=0,
        seat_id=group.seat_id,
        camera_id=group.camera_id,
        roi_name=group.roi_name,
        base_light_id=light_order[0],
        light_order=light_order,
        frames=frames,
        registration=registration,
        calibration_id=first_frame.calibration_id,
        roi_bbox_xyxy_pixel=first_frame.bbox_xyxy_pixel,
    )
    builder = feature_builder or FeatureBuilder()
    model_config = recipe.models[selected_model_key]
    return builder._make_feature_group(
        cube,
        selected_model_key,
        model_config,
        builder._build_feature_dict(cube, model_config.input_channels),
    )


def light_frame_from_manifest_row(row: ManifestRow, dataset_root: Path) -> LightFrame:
    image_path = resolve_image_path(dataset_root, row.image_path)
    image = read_gray_image(image_path)
    return LightFrame(
        camera_id=row.camera_id,
        pose_id=row.pose_id,
        light_id=row.light_id,
        frame_index=row.line_number,
        light_seq_index=row.line_number - 1,
        width=image.width,
        height=image.height,
        channels=1,
        stride_bytes=image.width,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=row.line_number * 100,
        exposure_us=0,
        gain=1.0,
        calibration_id="manifest_roi",
        image_crc32=0,
        image=memoryview(image.pixels),
        origin_xy=(0, 0),
        source_width=image.width,
        source_height=image.height,
    )


def resolve_image_path(dataset_root: Path, image_path: str) -> Path:
    path = Path(image_path)
    if not path.is_absolute():
        path = dataset_root / path
    if not path.is_file():
        raise TrainingDataError(f"样本图像不存在: {path}")
    return path


def read_gray_image(path: Path) -> RasterImage:
    try:
        return load_gray_image(path)
    except ImageCodecError as exc:
        raise TrainingDataError(str(exc)) from exc


def read_sample_image(path: Path) -> RasterImage:
    return read_gray_image(path)


def read_pgm(path: Path) -> RasterImage:
    return read_sample_image(path)


def _row_from_dict(raw: dict[str, Any], line_number: int, manifest_path: Path) -> ManifestRow:
    sample_id = _required_str(raw, "sample_id", line_number, manifest_path)
    light_id = _required_str(raw, "light_id", line_number, manifest_path)
    bboxes = _bbox_tuple(raw.get("bbox_xyxy_pixel", []), line_number, manifest_path, "bbox_xyxy_pixel")
    ground_truth = _ground_truth(raw, bboxes, line_number, manifest_path)
    return ManifestRow(
        line_number=line_number,
        sample_id=sample_id,
        source_trace_dir=str(raw.get("source_trace_dir", "")),
        recipe_id=str(raw.get("recipe_id", "")),
        seat_id=str(raw.get("seat_id", "")),
        sequence_id=_int(raw.get("sequence_id", 0), "sequence_id", line_number, manifest_path),
        decision=str(raw.get("decision", "")),
        quality_pass=bool(raw.get("quality_pass", False)),
        camera_id=_required_str(raw, "camera_id", line_number, manifest_path),
        pose_id=str(raw.get("pose_id") or raw.get("camera_id", "")),
        roi_name=_required_str(raw, "roi_name", line_number, manifest_path),
        light_id=light_id,
        image_path=_required_str(raw, "image_path", line_number, manifest_path),
        split=str(raw.get("split", "unassigned")),
        label_status=str(raw.get("label_status", "unlabeled")),
        has_defect=bool(raw.get("has_defect", bool(ground_truth))),
        bbox_xyxy_pixel=bboxes,
        ground_truth=ground_truth,
        metadata={key: value for key, value in raw.items() if key not in _KNOWN_FIELDS},
    )


def _ground_truth(
    raw: dict[str, Any],
    bboxes: tuple[tuple[int, int, int, int], ...],
    line_number: int,
    manifest_path: Path,
) -> tuple[dict[str, Any], ...]:
    raw_gt = raw.get("ground_truth")
    if raw_gt is not None:
        if not isinstance(raw_gt, list):
            raise TrainingDataError(f"{manifest_path}:{line_number}: ground_truth 必须是数组")
        return tuple(_normalize_gt(item, line_number, manifest_path) for item in raw_gt)

    gt_bboxes = _bbox_tuple(raw.get("ground_truth_bbox", []), line_number, manifest_path, "ground_truth_bbox")
    if gt_bboxes:
        return tuple(
            {
                "bbox_xyxy_pixel": list(bbox),
                "severity": str(raw.get("severity", "")),
                "roi_name": str(raw.get("roi_name", "")),
                "material": str(raw.get("material", "")),
                "color": str(raw.get("color", "")),
                "light_evidence": _str_tuple(raw.get("light_evidence", ())),
            }
            for bbox in gt_bboxes
        )

    if not bboxes:
        return ()
    return tuple(
        {
            "bbox_xyxy_pixel": list(bbox),
            "severity": str(raw.get("severity", "")),
            "roi_name": str(raw.get("roi_name", "")),
            "material": str(raw.get("material", "")),
            "color": str(raw.get("color", "")),
            "light_evidence": _str_tuple(raw.get("light_evidence", ())),
        }
        for bbox in bboxes
    )


def _normalize_gt(item: Any, line_number: int, manifest_path: Path) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise TrainingDataError(f"{manifest_path}:{line_number}: ground_truth item 必须是 object")
    bboxes = _bbox_tuple([item.get("bbox_xyxy_pixel")], line_number, manifest_path, "ground_truth.bbox_xyxy_pixel")
    return {
        "bbox_xyxy_pixel": list(bboxes[0]),
        "severity": str(item.get("severity", "")),
        "roi_name": str(item.get("roi_name", "")),
        "material": str(item.get("material", "")),
        "color": str(item.get("color", "")),
        "light_evidence": _str_tuple(item.get("light_evidence", ())),
    }


def _bbox_tuple(
    raw: Any,
    line_number: int,
    manifest_path: Path,
    field_name: str,
) -> tuple[tuple[int, int, int, int], ...]:
    if raw is None or raw == "" or raw == ():
        return ()
    if not isinstance(raw, (list, tuple)):
        raise TrainingDataError(f"{manifest_path}:{line_number}: {field_name} 必须是数组")
    if not raw:
        return ()
    result: list[tuple[int, int, int, int]] = []
    for bbox in raw:
        if not isinstance(bbox, list) or len(bbox) != 4:
            raise TrainingDataError(f"{manifest_path}:{line_number}: {field_name} bbox 必须是 4 元数组")
        values = tuple(int(value) for value in bbox)
        if values[2] < values[0] or values[3] < values[1]:
            raise TrainingDataError(f"{manifest_path}:{line_number}: {field_name} bbox 坐标反向: {values}")
        result.append(values)
    return tuple(result)


def _assert_group_consistent(rows: tuple[ManifestRow, ...], manifest_path: Path) -> None:
    first = rows[0]
    seen_lights: set[str] = set()
    for row in rows:
        if row.light_id in seen_lights:
            raise TrainingDataError(f"{manifest_path}:{row.line_number}: 重复光源: {row.light_id}")
        seen_lights.add(row.light_id)
        for field_name in ("source_trace_dir", "camera_id", "pose_id", "roi_name", "decision", "split", "label_status"):
            if getattr(row, field_name) != getattr(first, field_name):
                raise TrainingDataError(f"{manifest_path}:{row.line_number}: 样本组字段不一致: {field_name}")


def _base_sample_id(sample_id: str, light_id: str) -> str:
    suffix = f"_{light_id}"
    if sample_id.endswith(suffix):
        return sample_id[: -len(suffix)]
    return sample_id


def _required_str(raw: dict[str, Any], key: str, line_number: int, manifest_path: Path) -> str:
    value = str(raw.get(key, ""))
    if not value:
        raise TrainingDataError(f"{manifest_path}:{line_number}: 缺少 {key}")
    return value


def _str_tuple(raw: Any) -> tuple[str, ...]:
    if raw in (None, ""):
        return ()
    if isinstance(raw, str):
        return (raw,)
    if not isinstance(raw, (list, tuple)):
        return ()
    return tuple(str(value) for value in raw if str(value))


def _int(value: Any, key: str, line_number: int, manifest_path: Path) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise TrainingDataError(f"{manifest_path}:{line_number}: {key} 必须是整数") from exc


_KNOWN_FIELDS = {
    "sample_id",
    "source_trace_dir",
    "recipe_id",
    "seat_id",
    "sequence_id",
    "decision",
    "quality_pass",
    "camera_id",
    "pose_id",
    "roi_name",
    "light_id",
    "image_path",
    "has_defect",
    "bbox_xyxy_pixel",
    "split",
    "label_status",
    "ground_truth",
    "ground_truth_bbox",
    "severity",
    "material",
    "color",
    "light_evidence",
}
