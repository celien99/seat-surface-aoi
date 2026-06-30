param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [switch]$SkipDetector,
  [switch]$SkipDisplay,
  [switch]$CleanBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Import-Module -Force "$PSScriptRoot\module\SeatAoiDeployment\SeatAoiDeployment.psd1" -WarningAction SilentlyContinue

# ============================================================================
# Main
# ============================================================================

$ProjectRoot = Resolve-ProjectRoot -Value $ProjectRoot
$Python = Get-VenvPython -Root $ProjectRoot -PythonExeOverride $PythonExe
$BinDir = Join-Path $ProjectRoot "bin"
$BuildDir = Join-Path $ProjectRoot "build\pyinstaller"

Push-Location $ProjectRoot
try {
  # ---- ensure PyInstaller is installed ----
  $pyiCheck = Invoke-NativeQuiet @($Python, "-c", "import PyInstaller")
  if ($pyiCheck -ne 0) {
    throw "PyInstaller not found. Install it into the venv: uv pip install --python .venv\Scripts\python.exe pyinstaller>=6.0"
  }

  # ---- pre-build checks ----
  if (-not $SkipDetector) {
    Assert-PythonModulesAvailable `
      -PythonPath $Python `
      -Modules @("yaml", "numpy", "scipy", "onnxruntime", "faiss", "cv2") `
      -InstallHint "Run install_station.ps1 without -SkipPythonSync, or install the onnx/faiss/opencv extras into the selected venv."
    $entryDetector = Join-Path $ProjectRoot "python_detector\detector_main.py"
    if (-not (Test-Path -LiteralPath $entryDetector)) {
      throw "Entry script not found: $entryDetector`nSource files may have been deleted by -CleanPythonSource. Restore source files before rebuilding."
    }
  }
  if (-not $SkipDisplay) {
    Assert-PythonModulesAvailable `
      -PythonPath $Python `
      -Modules @("PySide6") `
      -InstallHint "Run install_station.ps1 without -SkipPythonSync, or install the display extra into the selected venv."
    $entryDisplay = Join-Path $ProjectRoot "display_app\main.py"
    if (-not (Test-Path -LiteralPath $entryDisplay)) {
      throw "Entry script not found: $entryDisplay`nSource files may have been deleted by -CleanPythonSource. Restore source files before rebuilding."
    }
  }

  # ---- clean old dist ----
  $detectorDist = Join-Path $BinDir "seat_aoi_detector.exe"
  $displayDist = Join-Path $BinDir "seat_aoi_display"

  if ($CleanBuild -and (Test-Path -LiteralPath $BuildDir)) {
    Remove-Item -Recurse -Force $BuildDir
  }
  if ((-not $SkipDetector) -and (Test-Path -LiteralPath $detectorDist)) {
    Remove-Item -LiteralPath $detectorDist -Force
  }
  if ((-not $SkipDisplay) -and (Test-Path -LiteralPath $displayDist)) {
    Remove-Item -Recurse -Force $displayDist
  }
  New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
  New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

  [string[]]$sharedHiddenImports = @(
    "--hidden-import", "numpy.core._methods",
    "--hidden-import", "numpy.lib.format",
    "--hidden-import", "scipy.ndimage",
    "--hidden-import", "scipy.ndimage._ni_support",
    "--hidden-import", "scipy.ndimage._nd_image",
    "--hidden-import", "scipy.signal",
    "--hidden-import", "scipy.signal._sigtools",
    "--hidden-import", "scipy.sparse.csgraph._validation"
  )

  # ---------------------------------------------------------------------------
  # detector: --onefile
  # ---------------------------------------------------------------------------
  if (-not $SkipDetector) {
    Write-Host "[BUILD] seat_aoi_detector.exe (--onefile)..."
    [string[]]$detectorArgs = @(
      $Python,
      "-m", "PyInstaller",
      "--noconfirm",
      "--log-level", "WARN"
    ) + $sharedHiddenImports + @(
      "--onefile",
      "--name", "seat_aoi_detector",
      "--distpath", $BinDir,
      "--workpath", (Join-Path $BuildDir "detector"),
      "--specpath", $BuildDir,
      "--collect-all", "onnxruntime",
      "--collect-all", "faiss",
      "--collect-all", "cv2",
      "--add-data", "$(Join-Path $ProjectRoot 'python_detector\config');python_detector/config",
      "--hidden-import", "cv2",
      "--hidden-import", "yaml",
      "--hidden-import", "python_detector",
      "--hidden-import", "python_detector.config",
      "--hidden-import", "python_detector.config.recipe_schema",
      "--hidden-import", "python_detector.config.schema_types",
      "--hidden-import", "python_detector.config.schema_validators",
      "--hidden-import", "python_detector.ipc",
      "--hidden-import", "python_detector.ipc.shm_protocol",
      "--hidden-import", "python_detector.models",
      "--hidden-import", "python_detector.models.patchcore",
      "--hidden-import", "python_detector.models.patchcore_model",
      "--hidden-import", "python_detector.models.spatial_utils",
      "--hidden-import", "python_detector.models.embedding",
      "--hidden-import", "python_detector.models.inference_engine",
      "--hidden-import", "python_detector.models.pca",
      "--hidden-import", "python_detector.pipeline",
      "--hidden-import", "python_detector.pipeline.pipeline",
      "--hidden-import", "python_detector.pipeline.preprocessor",
      "--hidden-import", "python_detector.pipeline.quality_gate",
      "--hidden-import", "python_detector.pipeline.roi_locator",
      "--hidden-import", "python_detector.pipeline.ecc_registration",
      "--hidden-import", "python_detector.pipeline.feature_builder",
      "--hidden-import", "python_detector.pipeline.reflectance_cube",
      "--hidden-import", "python_detector.pipeline.fusion_engine",
      "--hidden-import", "python_detector.pipeline.defect_filter",
      "--hidden-import", "python_detector.pipeline.rule_engine",
      "--hidden-import", "python_detector.trace",
      "--hidden-import", "python_detector.trace.trace_writer",
      "--hidden-import", "python_detector.trace.overlay_renderer",
      "--hidden-import", "python_detector.display_channel",
      "python_detector/detector_main.py"
    )
    Invoke-Native -ArgList $detectorArgs
    Write-Host "[OK] seat_aoi_detector.exe built."
  }

  # ---------------------------------------------------------------------------
  # display: --onedir
  # ---------------------------------------------------------------------------
  if (-not $SkipDisplay) {
    Write-Host "[BUILD] seat_aoi_display (--onedir)..."
    [string[]]$displayArgs = @(
      $Python,
      "-m", "PyInstaller",
      "--noconfirm",
      "--log-level", "WARN"
    ) + @(
      "--windowed",
      "--onedir",
      "--name", "seat_aoi_display",
      "--distpath", $BinDir,
      "--workpath", (Join-Path $BuildDir "display"),
      "--specpath", $BuildDir,
      "--collect-all", "PySide6",
      "--hidden-import", "PySide6.QtCore",
      "--hidden-import", "PySide6.QtGui",
      "--hidden-import", "PySide6.QtWidgets",
      "--hidden-import", "PySide6.QtQml",
      "--hidden-import", "PySide6.QtQuick",
      "--hidden-import", "PySide6.QtQuickControls2",
      "--hidden-import", "PySide6.QtNetwork",
      "--hidden-import", "display_app",
      "--hidden-import", "display_app.infrastructure",
      "--hidden-import", "display_app.infrastructure.image_provider",
      "--hidden-import", "display_app.services",
      "--hidden-import", "display_app.services.display_bridge",
      "--hidden-import", "display_app.services.image_loader",
      "--hidden-import", "display_app.services.manual_trigger_client",
      "--hidden-import", "display_app.services.operator_journal",
      "--hidden-import", "display_app.viewmodels",
      "--hidden-import", "display_app.viewmodels.main_viewmodel",
      "--add-data", "$(Join-Path $ProjectRoot 'display_app\qml');display_app/qml",
      "--add-data", "$(Join-Path $ProjectRoot 'display_app\resources');display_app/resources",
      "display_app/main.py"
    )
    Invoke-Native -ArgList $displayArgs
    Write-Host "[OK] seat_aoi_display built."
  }

  # ---- verify ----
  if (-not $SkipDetector) {
    if (-not (Test-Path -LiteralPath $detectorDist)) {
      throw "Detector build failed: $detectorDist not found"
    }
    Write-Host "[DONE] Detector : $detectorDist"
  }

  if (-not $SkipDisplay) {
    $displayExe = Join-Path $displayDist "seat_aoi_display.exe"
    if (-not (Test-Path -LiteralPath $displayExe)) {
      throw "Display build failed: $displayExe not found"
    }
    Write-Host "[DONE] Display  : $displayExe"
  }

  Write-Host "[DONE] Python package build completed."
} finally {
  Pop-Location
}
