param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$PyinstallerKey = "",
  [switch]$SkipDetector,
  [switch]$SkipDisplay,
  [switch]$CleanBuild
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
  param([string]$Value)
  if ($Value) {
    return (Resolve-Path -LiteralPath $Value).Path
  }
  return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Invoke-Native {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
  & $Command[0] @($Command | Select-Object -Skip 1)
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed, exit=${LASTEXITCODE}: $($Command -join ' ')"
  }
}

function Get-VenvPython {
  param([string]$Root)
  if ($PythonExe -and (Test-Path -LiteralPath $PythonExe)) {
    return $PythonExe
  }
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  throw "Python not found at .venv\Scripts\python.exe. Run uv sync first."
}

function Test-PyinstallerInstalled {
  param([string]$Python)
  & $Python -c "import PyInstaller; print(PyInstaller.__version__)" 2>$null
  return ($LASTEXITCODE -eq 0)
}

$ProjectRoot = Resolve-ProjectRoot -Value $ProjectRoot
$Python = Get-VenvPython -Root $ProjectRoot
$BinDir = Join-Path $ProjectRoot "bin"
$BuildDir = Join-Path $ProjectRoot "build\pyinstaller"

Push-Location $ProjectRoot
try {
  # 确保 PyInstaller 已安装
  if (-not (Test-PyinstallerInstalled -Python $Python)) {
    Write-Host "Installing PyInstaller..."
    Invoke-Native $Python -m pip install pyinstaller
  }

  # 清理上一次构建产物
  if ($CleanBuild) {
    if (Test-Path -LiteralPath $BuildDir) {
      Remove-Item -Recurse -Force $BuildDir
    }
  }
  New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
  New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null

  # 构建加密密钥参数
  $keyArg = @()
  if ($PyinstallerKey) {
    $keyArg = @("--key", $PyinstallerKey)
  }

  # ---------------------------------------------------------------------------
  # detector: --onefile 单文件打包
  # ---------------------------------------------------------------------------
  if (-not $SkipDetector) {
    Write-Host "Building seat_aoi_detector.exe (--onefile)..."
    $detectorArgs = @(
      "-m", "PyInstaller",
      "--onefile",
      "--name", "seat_aoi_detector",
      "--distpath", $BinDir,
      "--workpath", (Join-Path $BuildDir "detector"),
      "--specpath", $BuildDir,
      "--hidden-import", "numpy.core._methods",
      "--hidden-import", "numpy.lib.format",
      "--hidden-import", "scipy.ndimage",
      "--hidden-import", "scipy.ndimage._ni_support",
      "--hidden-import", "scipy.ndimage._nd_image",
      "--hidden-import", "scipy.signal",
      "--hidden-import", "scipy.signal._sigtools",
      "--hidden-import", "scipy.sparse.csgraph._validation",
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
    $detectorArgs = $keyArg + $detectorArgs
    Invoke-Native $Python @detectorArgs
    Write-Host "seat_aoi_detector.exe built successfully."
  }

  # ---------------------------------------------------------------------------
  # display: --onedir 目录打包（兼容 PySide6 QML 资源体系）
  # ---------------------------------------------------------------------------
  if (-not $SkipDisplay) {
    Write-Host "Building seat_aoi_display (--onedir)..."
    $displayArgs = @(
      "-m", "PyInstaller",
      "--onedir",
      "--name", "seat_aoi_display",
      "--distpath", $BinDir,
      "--workpath", (Join-Path $BuildDir "display"),
      "--specpath", $BuildDir,
      "--hidden-import", "PySide6.QtCore",
      "--hidden-import", "PySide6.QtGui",
      "--hidden-import", "PySide6.QtWidgets",
      "--hidden-import", "PySide6.QtQml",
      "--hidden-import", "PySide6.QtQuick",
      "--hidden-import", "PySide6.QtQuickControls2",
      "--hidden-import", "PySide6.QtQuickTemplates2",
      "--hidden-import", "PySide6.QtQuickLayouts",
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
      "--add-data", "display_app/qml;display_app/qml",
      "--add-data", "display_app/resources;display_app/resources",
      "display_app/main.py"
    )
    $displayArgs = $keyArg + $displayArgs
    Invoke-Native $Python @displayArgs
    Write-Host "seat_aoi_display built successfully."
  }

  # ---------------------------------------------------------------------------
  # 校验产物
  # ---------------------------------------------------------------------------
  if (-not $SkipDetector) {
    $detectorExe = Join-Path $BinDir "seat_aoi_detector.exe"
    if (-not (Test-Path -LiteralPath $detectorExe)) {
      throw "Detector build failed: $detectorExe not found"
    }
    Write-Host "Detector: $detectorExe"
  }

  if (-not $SkipDisplay) {
    $displayExe = Join-Path $BinDir "seat_aoi_display\seat_aoi_display.exe"
    if (-not (Test-Path -LiteralPath $displayExe)) {
      throw "Display build failed: $displayExe not found"
    }
    Write-Host "Display : $displayExe"
  }

  Write-Host "Python package build completed."
} finally {
  Pop-Location
}
