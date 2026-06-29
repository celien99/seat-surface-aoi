param(
  [string]$ProjectRoot = "",
  [string]$PythonExe = "",
  [string]$PyinstallerKey = "",
  [switch]$SkipDetector,
  [switch]$SkipDisplay,
  [switch]$CleanBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ============================================================================
# PS 5.1 compatible helpers -- native exe stderr must NOT raise terminate errors
# ============================================================================

function Invoke-NativeSilent {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Command[0] @($Command | Select-Object -Skip 1) 2>&1 | Out-Null
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $saved
  }
}

function Invoke-Native {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Command[0] @($Command | Select-Object -Skip 1) 2>&1 | ForEach-Object { Write-Host $_ }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      throw "Command failed, exit=${exitCode}: $($Command -join ' ')"
    }
  } finally {
    $ErrorActionPreference = $saved
  }
}

function Resolve-ProjectRoot {
  param([string]$Value)
  if ($Value) {
    return (Resolve-Path -LiteralPath $Value).Path
  }
  return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Get-VenvPython {
  param(
    [string]$Root,
    [string]$PythonExeOverride = ""
  )
  if ($PythonExeOverride -and (Test-Path -LiteralPath $PythonExeOverride)) {
    return $PythonExeOverride
  }
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (Test-Path -LiteralPath $venvPython) {
    return $venvPython
  }
  throw "Python not found at .venv\Scripts\python.exe. Run uv sync first."
}

function Test-PyinstallerInstalled {
  param([string]$Python)
  $exitCode = Invoke-NativeSilent $Python -c "import PyInstaller"
  return ($exitCode -eq 0)
}

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
  if (-not (Test-PyinstallerInstalled -Python $Python)) {
    Write-Host "[INFO] PyInstaller not found in venv, attempting install..."
    $installCode = Invoke-NativeSilent $Python -m pip install pyinstaller --no-input
    if ($installCode -ne 0) {
      Write-Host ""
      Write-Host "============================================================" -ForegroundColor Yellow
      Write-Host " PyInstaller install failed (offline environment?)" -ForegroundColor Yellow
      Write-Host "============================================================" -ForegroundColor Yellow
      Write-Host ""
      Write-Host " To install offline:"
      Write-Host "   1. On a networked PC: pip download pyinstaller -d pyinstaller_offline"
      Write-Host "   2. Copy pyinstaller_offline folder to this machine"
      Write-Host "   3. Run: $Python -m pip install --no-index --find-links pyinstaller_offline pyinstaller"
      Write-Host "   4. Re-run this script"
      Write-Host ""
      Write-Host "============================================================" -ForegroundColor Yellow
      throw "PyInstaller is not installed and cannot be downloaded (offline)."
    }
    Write-Host "[INFO] PyInstaller installed successfully."
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

  # ---- common PyInstaller args ----
  $commonArgs = @(
    "--noconfirm",
    "--log-level", "WARN"
  )

  $keyArg = @()
  if ($PyinstallerKey) {
    $keyArg = @("--key", $PyinstallerKey)
  }

  $sharedHiddenImports = @(
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
    $detectorArgs = @(
      "-m", "PyInstaller",
      "--onefile",
      "--name", "seat_aoi_detector",
      "--distpath", $BinDir,
      "--workpath", (Join-Path $BuildDir "detector"),
      "--specpath", $BuildDir,
      "--collect-all", "onnxruntime",
      "--collect-all", "faiss",
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
    $detectorArgs = $commonArgs + $keyArg + $sharedHiddenImports + $detectorArgs
    Invoke-Native $Python @detectorArgs
    Write-Host "[OK] seat_aoi_detector.exe built."
  }

  # ---------------------------------------------------------------------------
  # display: --onedir
  # ---------------------------------------------------------------------------
  if (-not $SkipDisplay) {
    Write-Host "[BUILD] seat_aoi_display (--onedir)..."
    $displayArgs = @(
      "-m", "PyInstaller",
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
    $displayArgs = $commonArgs + $keyArg + $displayArgs
    Invoke-Native $Python @displayArgs
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
