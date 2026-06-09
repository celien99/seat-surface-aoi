from __future__ import annotations

from pathlib import Path

from tools.validate_model_assets import load_recipe_by_id_or_path, validate_recipe_model_assets


def test_production_model_example_loads_but_reports_missing_assets() -> None:
    recipe = load_recipe_by_id_or_path("production_model_example")

    issues = validate_recipe_model_assets(recipe)

    assert recipe.recipe_id == "production_model_example"
    messages = [issue.message for issue in issues]
    assert any("YOLO ROI ONNX 文件为空或仍是占位文件" in message for message in messages)
    assert any("WideResNet50 embedding ONNX 文件为空或仍是占位文件" in message for message in messages)
    assert any("PatchCore memory bank 为空或仍是占位文件" in message for message in messages)


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
