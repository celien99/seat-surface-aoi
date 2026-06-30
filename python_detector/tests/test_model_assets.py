from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from tools.validate_model_assets import load_recipe_by_id_or_path, validate_recipe_model_assets
from python_detector import paths as detector_paths


def test_production_recipe_loads_full_model_chain() -> None:
    recipe = load_recipe_by_id_or_path("seat_a_black_leather_production_v1")

    assert recipe.recipe_id == "seat_a_black_leather_production_v1"
    assert recipe.quality.required_lights == ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    assert recipe.model_key_for("TOP_BACK", "seat") == "patchcore_detector"
    assert recipe.safety_net_model_keys_for("TOP_BACK", "seat") == ()
    assert recipe.models["patchcore_detector"].input_channels == (
        "light:DIFFUSE",
        "light:POLAR_DIFFUSE",
        "light:HIGH_LEFT",
    )
    assert recipe.roi_locator.backend == "onnx_yolo_seg"
    assert recipe.roi_locator.model_path == "model/roi_yolo/seat_roi_seg.onnx"
    assert recipe.models["patchcore_detector"].backend == "patchcore_knn"
    assert recipe.models["patchcore_detector"].role == "primary"
    assert recipe.models["patchcore_detector"].spatial_upsample_height == 128
    assert recipe.models["patchcore_detector"].spatial_upsample_width == 128
    assert recipe.models["patchcore_detector"].anomaly_binarize_min_ratio == 0.5
    assert recipe.models["patchcore_detector"].anomaly_binarize_relative == 0.55
    assert recipe.models["patchcore_detector"].score_threshold == 0.55


def test_model_relative_paths_resolve_to_project_model_without_cwd(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "seat-surface-aoi"
    model_path = project_root / "model" / "roi_yolo" / "seat_roi_seg.onnx"
    model_path.parent.mkdir(parents=True)
    model_path.write_bytes(b"onnx")
    monkeypatch.setattr(detector_paths, "PROJECT_ROOT", project_root)
    monkeypatch.chdir(tmp_path)

    assert detector_paths.resolve_runtime_path("model/roi_yolo/seat_roi_seg.onnx") == model_path


def test_production_robot_recipe_uses_patchcore_primary_detector() -> None:
    recipe = load_recipe_by_id_or_path("seat_a_robot_flyshot_production_v1")

    assert recipe.recipe_id == "seat_a_robot_flyshot_production_v1"
    assert recipe.registration.method == "ecc"
    assert recipe.roi_locator.backend == "onnx_yolo_seg"
    assert recipe.model_key_for("EYE_IN_HAND", "seat", "T1_BACKREST") == "patchcore_detector"
    assert recipe.safety_net_model_keys_for("EYE_IN_HAND", "seat", "T1_BACKREST") == ()


def test_validate_model_assets_reports_placeholder_files(tmp_path: Path) -> None:
    roi_yolo = tmp_path / "seat_roi_seg.onnx"
    embedding = tmp_path / "seat_wrn50_embedding.onnx"
    pca = tmp_path / "seat_pca.json"
    bank = tmp_path / "seat_patchcore_bank.json"
    faiss = tmp_path / "seat_patchcore.faiss"
    for path in (roi_yolo, embedding, pca, bank, faiss):
        path.write_bytes(b"0")
    recipe_path = tmp_path / "placeholder_recipe.yaml"
    recipe_path.write_text(
        f"""
recipe_id: placeholder_assets
sku: sku
light_order: [DIFFUSE, POLAR_DIFFUSE, HIGH_LEFT, HIGH_RIGHT]
roi_locator:
  backend: onnx_yolo_seg
  model_path: {roi_yolo}
  output_decode: ultralytics_yolo_seg
cameras:
  TOP:
    model_key: patchcore
decision_threshold:
  ng_score: 0.55
  recheck_score: 0.20
  min_area_px: 1
models:
  default:
    backend: fake
    role: primary
  patchcore:
    backend: patchcore_knn
    model_family: patchcore
    role: primary
    embedding_backend: onnx_wideresnet50
    embedding_model_path: {embedding}
    pca_path: {pca}
    pca_version: pca_v1
    memory_bank_path: {bank}
    faiss_index_path: {faiss}
""",
        encoding="utf-8",
    )
    recipe = load_recipe_by_id_or_path(str(recipe_path))

    messages = [issue.message for issue in validate_recipe_model_assets(recipe)]

    assert any("YOLO ROI segmentation ONNX 文件为空或仍是占位文件" in message for message in messages)
    assert any("WideResNet50 embedding ONNX 文件为空或仍是占位文件" in message for message in messages)
    assert any("PCA 参数文件为空或仍是占位文件" in message for message in messages)
    assert any("PatchCore memory bank 为空或仍是占位文件" in message for message in messages)
    assert any("PatchCore FAISS index 文件为空或仍是占位文件" in message for message in messages)


def test_validate_model_assets_accepts_valid_pca_and_memory_bank(tmp_path: Path) -> None:
    pca_path = tmp_path / "pca.json"
    pca_path.write_text(
        '{"version":"pca_v1","mean":[0.0,0.0],"components":[[1.0,0.0]]}',
        encoding="utf-8",
    )
    bank_path = tmp_path / "bank.json"
    _write_patchcore_bank(bank_path, np.asarray([[0.0]], dtype=np.float32), pca_version="pca_v1")
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        f"""
recipe_id: asset_ok
sku: sku
light_order: [DIFFUSE, POLAR_DIFFUSE, HIGH_LEFT, HIGH_RIGHT]
cameras:
  TOP:
    model_key: default
    safety_net_model_key: patchcore
decision_threshold:
  ng_score: 0.55
  recheck_score: 0.20
  min_area_px: 1
models:
  default:
    backend: fake
    role: primary
  patchcore:
    backend: patchcore_knn
    model_family: patchcore
    role: safety_net
    embedding_backend: statistical
    embedding_dim: 2
    pca_path: {pca_path}
    pca_version: pca_v1
    memory_bank_path: {bank_path}
""",
        encoding="utf-8",
    )
    recipe = load_recipe_by_id_or_path(str(recipe_path))

    assert validate_recipe_model_assets(recipe) == []


def test_validate_model_assets_rejects_patchcore_dimension_mismatch(tmp_path: Path) -> None:
    pca_path = tmp_path / "pca.json"
    pca_path.write_text(
        '{"version":"pca_v1","mean":[0.0,0.0],"components":[[1.0,0.0],[0.0,1.0]]}',
        encoding="utf-8",
    )
    bank_path = tmp_path / "bank.json"
    _write_patchcore_bank(bank_path, np.asarray([[0.0, 0.0, 0.0]], dtype=np.float32), pca_version="pca_v1")
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        f"""
recipe_id: asset_bad_dim
sku: sku
light_order: [DIFFUSE, POLAR_DIFFUSE, HIGH_LEFT, HIGH_RIGHT]
cameras:
  TOP:
    model_key: patchcore
decision_threshold:
  ng_score: 0.55
  recheck_score: 0.20
  min_area_px: 1
models:
  default:
    backend: fake
    role: primary
  patchcore:
    backend: patchcore_knn
    model_family: patchcore
    role: primary
    embedding_backend: statistical
    embedding_dim: 2
    pca_path: {pca_path}
    pca_version: pca_v1
    memory_bank_path: {bank_path}
""",
        encoding="utf-8",
    )
    recipe = load_recipe_by_id_or_path(str(recipe_path))

    messages = [issue.message for issue in validate_recipe_model_assets(recipe)]

    assert any("PCA 输出维度与 PatchCore memory bank 维度不匹配: 2 != 3" in message for message in messages)


def test_validate_model_assets_rejects_patchcore_metadata_mismatch(tmp_path: Path) -> None:
    embedding = tmp_path / "embedding.onnx"
    embedding.write_bytes(b"onnx")
    pca_path = tmp_path / "pca.json"
    pca_path.write_text(
        '{"version":"pca_v1","mean":[0.0,0.0],"components":[[1.0,0.0]]}',
        encoding="utf-8",
    )
    bank_path = tmp_path / "bank.json"
    _write_patchcore_bank(
        bank_path,
        np.asarray([[0.0]], dtype=np.float32),
        pca_version="pca_v1",
        metadata={
            "input_channels": ["light:DIFFUSE", "light:HIGH_LEFT"],
            "spatial_mode": True,
            "spatial_layers": ["layer2", "layer3"],
            "spatial_upsample_height": 32,
            "spatial_upsample_width": 64,
        },
    )
    recipe_path = tmp_path / "recipe.yaml"
    recipe_path.write_text(
        f"""
recipe_id: asset_bad_metadata
sku: sku
light_order: [DIFFUSE, POLAR_DIFFUSE, HIGH_LEFT]
cameras:
  TOP:
    model_key: patchcore
decision_threshold:
  ng_score: 0.55
  recheck_score: 0.20
  min_area_px: 1
models:
  default:
    backend: fake
    role: primary
  patchcore:
    backend: patchcore_knn
    model_family: patchcore
    role: primary
    input_channels: [light:DIFFUSE, light:POLAR_DIFFUSE, light:HIGH_LEFT]
    embedding_backend: onnx_wideresnet50
    embedding_model_path: {embedding}
    embedding_dim: 2
    pca_path: {pca_path}
    pca_version: pca_v1
    memory_bank_path: {bank_path}
    spatial_mode: true
    spatial_layers: [layer2, layer3]
    spatial_upsample_height: 64
    spatial_upsample_width: 64
""",
        encoding="utf-8",
    )
    recipe = load_recipe_by_id_or_path(str(recipe_path))

    messages = [issue.message for issue in validate_recipe_model_assets(recipe)]

    assert any("PatchCore input_channels 与配方不匹配" in message for message in messages)
    assert any("PatchCore spatial_upsample_height 与配方不匹配: 32 != 64" in message for message in messages)


def _write_patchcore_bank(
    bank_path: Path,
    vectors: np.ndarray,
    *,
    pca_version: str | None,
    metadata: dict | None = None,
) -> None:
    vectors_path = bank_path.with_suffix(".npy")
    np.save(vectors_path, np.asarray(vectors, dtype=np.float32))
    payload = {
        "version": "bank_v1",
        "model_family": "patchcore",
        "embedding_dim": int(vectors.shape[1]),
        "coreset_ratio": 1.0,
        "pca_version": pca_version,
        "faiss_enabled": False,
        "vector_count": int(vectors.shape[0]),
        "vectors_path": vectors_path.name,
    }
    if metadata is not None:
        payload["metadata"] = metadata
    bank_path.write_text(json.dumps(payload), encoding="utf-8")
