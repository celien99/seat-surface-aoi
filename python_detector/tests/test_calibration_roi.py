import pytest
from dataclasses import replace
from pathlib import Path

from python_detector.config.calibration_manager import CalibrationManager
from python_detector.config.recipe_schema import RecipeManager, RecipeValidationError
from python_detector.ipc.data_types import CameraBundle, LightFrame, SeatInspectionJob
from python_detector.pipeline.pipeline import InspectionPipeline
from python_detector.pipeline.preprocessor import Preprocessor
from python_detector.pipeline.reflectance_cube import ReflectanceCubeBuilder


LIGHT_ORDER = ("DIFFUSE", "POLAR_DIFFUSE", "HIGH_LEFT", "HIGH_RIGHT")


def _frame(light_id: str, calibration_id: str = "calib/simulated_v1", camera_id: str = "TOP_BACK") -> LightFrame:
    frame_index = LIGHT_ORDER.index(light_id) + 1 if light_id in LIGHT_ORDER else 1
    data = bytearray(
        80 + (((x // 2 + y // 2) % 2) * 20) + ((x + 3 * y) % 12)
        for y in range(48)
        for x in range(64)
    )
    return LightFrame(
        camera_id=camera_id,
        light_id=light_id,
        frame_index=frame_index,
        light_seq_index=frame_index - 1,
        width=64,
        height=48,
        channels=1,
        stride_bytes=64,
        pixel_format="MONO8",
        bit_depth=8,
        color_order="MONO",
        dtype="UINT8",
        timestamp_us=1_000 + (frame_index - 1) * 100,
        exposure_us=800,
        gain=1.0,
        calibration_id=calibration_id,
        image_crc32=0,
        image=memoryview(data),
    )


def test_calibration_manager_loads_identity_roi() -> None:
    calibration = CalibrationManager().load(
        "TOP_BACK",
        "calib/simulated_v1",
        "python_detector/config/roi/default_roi.yaml",
    )
    assert calibration.roi_templates["seat"].output_size == (64, 48)
    assert calibration.light_alignment["DIFFUSE"] == (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)


def test_calibration_manager_default_path_is_independent_from_cwd(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    calibration = CalibrationManager().load(
        "TOP_BACK",
        "calib/simulated_v1",
        "python_detector/config/roi/default_roi.yaml",
    )

    assert calibration.camera_id == "TOP_BACK"
    assert calibration.roi_templates["seat"].output_size == (64, 48)


def test_calibration_manager_cache_is_scoped_by_roi_template_path(tmp_path: Path) -> None:
    roi_a = tmp_path / "roi_a.yaml"
    roi_b = tmp_path / "roi_b.yaml"
    roi_a.write_text(
        """
roi_templates:
  a:
    polygon_xy:
      - [0, 0]
      - [9, 0]
      - [9, 9]
      - [0, 9]
    output_size: [10, 10]
""",
        encoding="utf-8",
    )
    roi_b.write_text(
        """
roi_templates:
  b:
    polygon_xy:
      - [1, 1]
      - [8, 1]
      - [8, 8]
      - [1, 8]
    output_size: [8, 8]
""",
        encoding="utf-8",
    )
    manager = CalibrationManager()

    calibration_a = manager.load("TOP_BACK", "calib/simulated_v1", str(roi_a))
    calibration_b = manager.load("TOP_BACK", "calib/simulated_v1", str(roi_b))

    assert set(calibration_a.roi_templates) == {"a"}
    assert set(calibration_b.roi_templates) == {"b"}


def test_calibration_manager_rejects_missing_roi_template_file() -> None:
    with pytest.raises(RecipeValidationError, match="ROI 模板文件不存在"):
        CalibrationManager().load("TOP_BACK", "calib/simulated_v1", "python_detector/config/roi/missing.yaml")


def test_calibration_manager_rejects_invalid_roi_template(tmp_path: Path) -> None:
    invalid_output_size = tmp_path / "invalid_output_size.yaml"
    invalid_output_size.write_text(
        """
roi_templates:
  bad:
    polygon_xy:
      - [0, 0]
      - [9, 0]
      - [9, 9]
      - [0, 9]
    output_size: [0, 10]
""",
        encoding="utf-8",
    )
    repeated_point = tmp_path / "repeated_point.yaml"
    repeated_point.write_text(
        """
roi_templates:
  bad:
    polygon_xy:
      - [0, 0]
      - [9, 0]
      - [9, 0]
      - [0, 9]
    output_size: [10, 10]
""",
        encoding="utf-8",
    )
    degenerate_polygon = tmp_path / "degenerate_polygon.yaml"
    degenerate_polygon.write_text(
        """
roi_templates:
  bad:
    polygon_xy:
      - [0, 0]
      - [4, 0]
      - [8, 0]
    output_size: [10, 10]
""",
        encoding="utf-8",
    )

    manager = CalibrationManager()
    with pytest.raises(RecipeValidationError, match="output_size.width"):
        manager.load("TOP_BACK", "calib/simulated_v1", str(invalid_output_size))
    with pytest.raises(RecipeValidationError, match="ROI 存在重复点"):
        manager.load("TOP_BACK", "calib/simulated_v1", str(repeated_point))
    with pytest.raises(RecipeValidationError, match="ROI 面积无效"):
        manager.load("TOP_BACK", "calib/simulated_v1", str(degenerate_polygon))


def test_calibration_manager_rejects_invalid_alignment_matrix(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "python_detector/config/calibration/TOP_BACK"
    calibration_dir.mkdir(parents=True)
    (calibration_dir / "invalid_matrix.yaml").write_text(
        """
calibration_id: calib/invalid_matrix
camera_id: TOP_BACK
image_size:
  width: 64
  height: 48
pixel_size_mm: 0.12
base_light_id: POLAR_DIFFUSE
light_alignment:
  DIFFUSE:
    matrix_3x3: [1, 0, 0]
roi_templates:
  seat:
    polygon_xy:
      - [0, 0]
      - [63, 0]
      - [63, 47]
      - [0, 47]
    output_size: [64, 48]
""",
        encoding="utf-8",
    )

    with pytest.raises(RecipeValidationError, match="matrix_3x3 必须包含 9 个数字"):
        CalibrationManager(tmp_path).load(
            "TOP_BACK",
            "calib/invalid_matrix",
            "python_detector/config/roi/default_roi.yaml",
        )


def test_calibration_mismatch_returns_error_not_ok() -> None:
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    frames = {
        light: _frame(light, calibration_id="calib/wrong")
        for light in LIGHT_ORDER
    }
    cushion_frames = {
        light: _frame(light, calibration_id="calib/wrong", camera_id="TOP_CUSHION")
        for light in LIGHT_ORDER
    }
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[
            CameraBundle(camera_id="TOP_BACK", pose_id="TOP_BACK", light_frames=frames),
            CameraBundle(camera_id="TOP_CUSHION", pose_id="TOP_CUSHION", light_frames=cushion_frames),
        ],
    )
    result = InspectionPipeline().process(job, recipe)
    assert result.decision == "ERROR"
    assert result.quality_pass is False


def test_preprocessor_crops_roi_and_preserves_source_bbox(tmp_path: Path) -> None:
    roi_path = tmp_path / "roi.yaml"
    roi_path.write_text(
        """
roi_templates:
  center:
    polygon_xy:
      - [10, 8]
      - [25, 8]
      - [25, 19]
      - [10, 19]
    output_size: [16, 12]
""",
        encoding="utf-8",
    )
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    camera = replace(recipe.cameras[0], roi_template=str(roi_path))
    recipe = replace(recipe, cameras=(camera,))
    frames = {
        light: _frame(light)
        for light in LIGHT_ORDER
    }
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[CameraBundle(camera_id="TOP_BACK", pose_id="TOP_BACK", light_frames=frames)],
    )

    prepared = Preprocessor().run(job, recipe)
    roi_frame = prepared[0].rois["center"]["DIFFUSE"]

    assert roi_frame.width == 16
    assert roi_frame.height == 12
    assert roi_frame.origin_xy == (10, 8)
    assert roi_frame.bbox_xyxy_pixel == (10, 8, 25, 19)
    assert roi_frame.roi_to_source_matrix == (1.0, 0.0, 10.0, 0.0, 1.0, 8.0, 0.0, 0.0, 1.0)
    assert roi_frame.source_to_roi_matrix == (1.0, 0.0, -10.0, 0.0, 1.0, -8.0, 0.0, 0.0, 1.0)
    assert int(roi_frame.image[0]) == int(frames["DIFFUSE"].image[8 * 64 + 10])


def test_preprocessor_warps_four_point_roi_to_output_size(tmp_path: Path) -> None:
    roi_path = tmp_path / "roi.yaml"
    roi_path.write_text(
        """
roi_templates:
  tilted:
    polygon_xy:
      - [10, 8]
      - [30, 6]
      - [33, 21]
      - [8, 23]
    output_size: [8, 6]
""",
        encoding="utf-8",
    )
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    camera = replace(recipe.cameras[0], roi_template=str(roi_path))
    recipe = replace(recipe, cameras=(camera,))
    frames = {light: _frame(light) for light in LIGHT_ORDER}
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[CameraBundle(camera_id="TOP_BACK", pose_id="TOP_BACK", light_frames=frames)],
    )

    prepared = Preprocessor().run(job, recipe)
    roi_frame = prepared[0].rois["tilted"]["DIFFUSE"]

    assert roi_frame.width == 8
    assert roi_frame.height == 6
    assert roi_frame.origin_xy == (8, 6)
    assert roi_frame.bbox_xyxy_pixel == (8, 6, 33, 23)
    assert roi_frame.roi_to_source_matrix is not None
    assert roi_frame.source_to_roi_matrix is not None
    assert len(roi_frame.image) == 8 * 6
    assert max(roi_frame.image) > min(roi_frame.image)


def test_registration_error_exceeding_threshold_returns_recheck(tmp_path: Path) -> None:
    calibration_dir = tmp_path / "python_detector/config/calibration/TOP_BACK"
    calibration_dir.mkdir(parents=True)
    roi_dir = tmp_path / "python_detector/config/roi"
    roi_dir.mkdir(parents=True)
    (roi_dir / "default_roi.yaml").write_text(
        """
roi_templates:
  seat:
    polygon_xy:
      - [0, 0]
      - [63, 0]
      - [63, 47]
      - [0, 47]
    output_size: [64, 48]
""",
        encoding="utf-8",
    )
    calibration_path = calibration_dir / "shifted.yaml"
    calibration_path.write_text(
        """
calibration_id: calib/shifted
camera_id: TOP_BACK
image_size:
  width: 64
  height: 48
pixel_size_mm: 0.12
base_light_id: POLAR_DIFFUSE
light_alignment:
  DIFFUSE:
    matrix_3x3: [1, 0, 0, 0, 1, 0, 0, 0, 1]
  POLAR_DIFFUSE:
    matrix_3x3: [1, 0, 0, 0, 1, 0, 0, 0, 1]
  HIGH_LEFT:
    matrix_3x3: [1, 0, 3, 0, 1, 0, 0, 0, 1]
  HIGH_RIGHT:
    matrix_3x3: [1, 0, 0, 0, 1, 0, 0, 0, 1]
roi_templates:
  seat:
    polygon_xy:
      - [0, 0]
      - [63, 0]
      - [63, 47]
      - [0, 47]
    output_size: [64, 48]
""",
        encoding="utf-8",
    )
    recipe = RecipeManager().load("seat_a_black_leather_v1")
    camera = replace(recipe.cameras[0], calibration_id="calib/shifted")
    recipe = replace(recipe, cameras=(camera,))
    frames = {
        light: _frame(light, calibration_id="calib/shifted")
        for light in LIGHT_ORDER
    }
    job = SeatInspectionJob(
        sequence_id=1,
        trigger_id=2,
        seat_id="SIM",
        recipe_id=recipe.recipe_id,
        sku=recipe.sku,
        camera_bundles=[CameraBundle(camera_id="TOP_BACK", pose_id="TOP_BACK", light_frames=frames)],
    )
    preprocessor = Preprocessor(CalibrationManager(tmp_path))
    pipeline = InspectionPipeline(preprocessor=preprocessor, reflectance_cube_builder=ReflectanceCubeBuilder())

    result = pipeline.process(job, recipe)

    assert result.decision == "RECHECK"
    assert result.quality_pass is False
    assert pipeline.last_context["registration_reports"][0].max_error_px == pytest.approx(3.0)
