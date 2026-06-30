from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _script_text(name: str) -> str:
    return (REPO_ROOT / "tools" / "windows" / name).read_text(encoding="utf-8")


def test_install_station_installs_pyinstaller_when_packaging() -> None:
    text = _script_text("install_station.ps1")

    assert "-IncludePyInstaller ([bool]$BuildPythonPackages)" in text
    assert "pyinstaller>=6.0" in text


def test_install_station_installs_opencv_for_packaged_detector() -> None:
    install_text = _script_text("install_station.ps1")
    build_text = _script_text("build_python_packages.ps1")

    assert '"--extra", "opencv"' in install_text
    assert '"opencv-python"' in install_text
    assert '@("yaml", "numpy", "scipy", "onnxruntime", "faiss", "cv2")' in build_text
    assert '@("yaml", "numpy", "scipy", "onnxruntime", "faiss", "cv2", "PySide6")' in install_text
    assert '"--collect-all", "cv2"' in build_text
    assert '"--hidden-import", "cv2"' in build_text


def test_install_station_rejects_unsupported_python_versions() -> None:
    text = _script_text("install_station.ps1")

    assert "Assert-PythonVersionSupported -PythonPath $venvPython" in text
    venv_python_index = text.index("$VenvPython = Get-VenvPython -Root $ProjectRoot")
    version_check_index = text.index("Assert-PythonVersionSupported -PythonPath $VenvPython", venv_python_index)
    module_check_index = text.index("Assert-PythonModulesAvailable `", version_check_index)

    assert venv_python_index < version_check_index < module_check_index
    assert "-PythonPath $VenvPython" in text[module_check_index:]
    assert "请不要使用 -SkipPythonSync" in text
    assert "请使用 Python 3.10-3.12" in text
    assert '"--python", $ExplicitPython' in text


def test_build_python_packages_fails_fast_when_packaging_dependencies_are_missing() -> None:
    text = _script_text("build_python_packages.ps1")

    assert "function Assert-PythonModulesAvailable" in text
    assert "importlib.import_module(name)" in text
    assert "Missing or unloadable Python modules:" in text
    assert "Python package dependencies are incomplete for PyInstaller packaging" in text
    assert 'Run install_station.ps1 without -SkipPythonSync, or install the onnx/faiss/opencv extras' in text
    assert 'Run install_station.ps1 without -SkipPythonSync, or install the display extra' in text


def test_install_station_defaults_data_and_model_roots_to_project_drive() -> None:
    text = _script_text("install_station.ps1")

    assert 'Resolve-DeploymentRoot -Value $DataRoot -DefaultLeafName "seat-aoi-data" -Root $ProjectRoot' in text
    assert 'Resolve-DeploymentRoot -Value $ModelRoot -DefaultLeafName "seat-aoi-model" -Root $ProjectRoot' in text
    assert "Resolve-DefaultRootOnProjectDrive -Root $Root -LeafName $DefaultLeafName" in text
    assert '[string]$DataRoot = ""' in text
    assert '[string]$ModelRoot = ""' in text
    assert "$Value -match '^[\\\\/][^\\\\/]'" in text
    assert "$rootRelative = $Value -replace '^[\\\\/]+', ''" in text
    assert "Join-Path $projectDrive $rootRelative" in text
    assert "$Value -match '^[A-Za-z]:[^\\\\/]'" in text
    assert "not drive-relative" in text


def test_uninstall_station_uses_same_project_drive_root_resolution() -> None:
    text = _script_text("uninstall_station.ps1")

    assert 'Resolve-DeploymentRoot -Value $DataRoot -DefaultLeafName "seat-aoi-data" -Root $ProjectRoot' in text
    assert 'Resolve-DeploymentRoot -Value $ModelRoot -DefaultLeafName "seat-aoi-model" -Root $ProjectRoot' in text
    assert "$Value -match '^[\\\\/][^\\\\/]'" in text
    assert "$rootRelative = $Value -replace '^[\\\\/]+', ''" in text
    assert "Join-Path $projectDrive $rootRelative" in text
    assert "$Value -match '^[A-Za-z]:[^\\\\/]'" in text
    assert "not drive-relative" in text


def test_install_station_injects_recipe_paths_before_pyinstaller_build() -> None:
    text = _script_text("install_station.ps1")

    recipe_update = text.index("Update-RecipeModelPaths -RecipePath $recipePath")
    package_build = text.index("$pyinstallerScript = Join-Path $ProjectRoot")

    assert recipe_update < package_build


def test_install_station_resolves_active_recipe_from_cpp_config() -> None:
    text = _script_text("install_station.ps1")

    assert 'Get-StationConfigValue -ConfigPath $ConfigFullPath -Key "recipe_id"' in text
    assert "Resolve-ActiveRecipePath -RecipeDir $recipeDir -RecipeArg $Recipe -RecipeId $ActiveRecipeId" in text
    assert "Active recipe YAML not found" in text
    assert "Do not continue with unpatched recipe paths" in text


def test_install_station_smoke_checks_packaged_detector_config() -> None:
    text = _script_text("install_station.ps1")

    assert "@($DetectorExe, \"--config\", $ConfigPath, \"--recipe-dir\", $recipeDir, \"--validate-config-only\")" in text
    assert "@($VenvPython, \"-m\", \"python_detector.detector_main\", \"--config\", $ConfigPath" in text
    assert '"tools.validate_model_assets", "--recipe", $recipePath' in text


def test_install_station_recipe_path_injection_is_generic_and_asserted() -> None:
    text = _script_text("install_station.ps1")

    assert "Convert-RecipeAssetPath -Value $matches[3] -ModelRoot $ModelRoot" in text
    assert "Cannot map recipe asset path to ModelRoot" in text
    assert "Assert-RecipeDeploymentPaths -RecipePath $recipePath -ModelRoot $ModelRoot -DataRoot $DataRoot" in text
    assert "$value -notmatch '^(?:[A-Za-z]:[\\\\/]|\\\\\\\\)'" in text
    assert "Recipe path is outside ModelRoot after injection" in text
    assert "Recipe trace root mismatch after injection" in text


def test_install_station_model_copy_does_not_overwrite_real_deployed_assets() -> None:
    text = _script_text("install_station.ps1")

    assert "Test-PlaceholderFile -Path $src" in text
    assert "Model source is placeholder, skip" in text
    assert "Model already exists, keep existing" in text
