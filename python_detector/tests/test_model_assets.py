from __future__ import annotations

from pathlib import Path

from tools.validate_model_assets import load_recipe_by_id_or_path, validate_recipe_model_assets


def test_production_recipe_loads_full_model_chain() -> None:
    recipe = load_recipe_by_id_or_path("seat_a_black_leather_production_v1")

    assert recipe.recipe_id == "seat_a_black_leather_production_v1"
    assert recipe.quality.required_lights == ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT")
    assert recipe.models["supervised_defect_onnx"].input_channels == (
        "ch0_diffuse",
        "ch1_polar_diffuse",
        "ch2_high_left",
    )
    assert recipe.roi_locator.backend == "onnx_yolo"
    assert recipe.models["supervised_defect_onnx"].backend == "onnx"
    assert recipe.models["patchcore_unknown_safety_net"].backend == "patchcore_knn"


def test_production_robot_recipe_uses_patchcore_safety_net() -> None:
    recipe = load_recipe_by_id_or_path("seat_a_robot_flyshot_production_v1")

    assert recipe.recipe_id == "seat_a_robot_flyshot_production_v1"
    assert recipe.registration.method == "ecc"
    assert recipe.roi_locator.backend == "onnx_yolo"
    assert recipe.safety_net_model_keys_for("EYE_IN_HAND", "full", "T1_BACKREST") == (
        "patchcore_unknown_safety_net",
    )


def test_validate_model_assets_reports_placeholder_files(tmp_path: Path) -> None:
    roi_yolo = tmp_path / "seat_roi_yolo.onnx"
    detector = tmp_path / "seat_defect_detector.onnx"
    embedding = tmp_path / "seat_wrn50_embedding.onnx"
    pca = tmp_path / "seat_pca.json"
    bank = tmp_path / "seat_patchcore_bank.json"
    faiss = tmp_path / "seat_patchcore.faiss"
    for path in (roi_yolo, detector, embedding, pca, bank, faiss):
        path.write_bytes(b"0")
    recipe_path = tmp_path / "placeholder_recipe.yaml"
    recipe_path.write_text(
        f"""
recipe_id: placeholder_assets
sku: sku
light_order: [DIFFUSE, POLAR_DIFFUSE, HIGH_LEFT, HIGH_RIGHT]
roi_locator:
  backend: onnx_yolo
  model_path: {roi_yolo}
cameras:
  TOP:
    model_key: detector
    safety_net_model_key: patchcore
thresholds:
  scratch: {{ng_score: 0.35, recheck_score: 0.20, min_area_px: 8}}
  unknown_anomaly: {{ng_score: 0.55, recheck_score: 0.20, min_area_px: 1}}
models:
  detector:
    backend: onnx
    model_path: {detector}
    role: primary
    class_names: [scratch]
    output_decode: ultralytics_yolo
  patchcore:
    backend: patchcore_knn
    model_family: patchcore
    role: safety_net
    class_names: [unknown_anomaly]
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

    assert any("YOLO ROI ONNX 文件为空或仍是占位文件" in message for message in messages)
    assert any("ONNX detection 文件为空或仍是占位文件" in message for message in messages)
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
    bank_path.write_text(
        '{"version":"bank_v1","model_family":"patchcore","embedding_dim":1,'
        '"coreset_ratio":1.0,"pca_version":"pca_v1","vectors":[[0.0]]}',
        encoding="utf-8",
    )
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
thresholds:
  scratch: {{ng_score: 0.35, recheck_score: 0.20, min_area_px: 8}}
  unknown_anomaly: {{ng_score: 0.55, recheck_score: 0.20, min_area_px: 1}}
models:
  default:
    backend: fake
    role: primary
    class_names: [scratch]
  patchcore:
    backend: patchcore_knn
    model_family: patchcore
    role: safety_net
    class_names: [unknown_anomaly]
    embedding_backend: statistical
    pca_path: {pca_path}
    pca_version: pca_v1
    memory_bank_path: {bank_path}
""",
        encoding="utf-8",
    )
    recipe = load_recipe_by_id_or_path(str(recipe_path))

    assert validate_recipe_model_assets(recipe) == []
