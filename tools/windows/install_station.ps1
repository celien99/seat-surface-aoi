param(
  [string]$ProjectRoot = "",
  [string]$ConfigPath = "cpp_controller\config\station_runtime.production.conf",
  [string]$TraceRoot = "trace",
  [string]$LineId = "LINE1_AOI_01",
  [ValidatePattern("^\d+x\d+$")]
  [string]$GridLayout = "2x1",
  [string]$Recipe = "seat_a_black_leather_production_v1",
  [string]$DetectorServiceName = "SeatAoiDetector",
  [string]$ControllerServiceName = "SeatAoiController",
  [string]$ShortcutName = "Seat AOI Display",
  [string]$NssmPath = "",
  [string]$PythonExe = "",
  [switch]$SkipPythonSync,
  [switch]$BuildController,
  [switch]$EnableHikrobotMvs,
  [string]$HikrobotIncludeDir = "C:\Program Files (x86)\MVS\Development\Includes",
  [string]$HikrobotLibrary = "C:\Program Files (x86)\MVS\Development\Libraries\win64\MvCameraControl.lib",
  [switch]$SkipValidation,
  [switch]$NoStartServices,
  [switch]$CurrentUserShortcut,
  [switch]$CreateStartupShortcut,
  [switch]$EnableDisplayManualTrigger,
  [string]$ManualTriggerHost = "127.0.0.1",
  [int]$ManualTriggerPort = 9000,
  [int]$ManualTriggerTimeoutMs = 1000,
  [switch]$BuildPythonPackages,
  [string]$PyinstallerKey = "",
  [string]$DataRoot = "D:\seat-aoi-data",
  [string]$ModelRoot = "D:\seat-aoi-model",
  [switch]$CleanPythonSource
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
  param([string]$Value)
  if ($Value) {
    return (Resolve-Path -LiteralPath $Value).Path
  }
  return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Test-IsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Invoke-Native {
  <#
    # PS 5.1: native exe stderr triggers terminate error under Stop mode.
    # Temporarily switch to Continue, merge stderr->stdout, then restore.
  #>
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

function Invoke-NativeOptional {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $Command[0] @($Command | Select-Object -Skip 1) 2>&1 | Out-Null
  } finally {
    $ErrorActionPreference = $saved
  }
}

function Resolve-Nssm {
  param([string]$ExplicitPath, [string]$Root)
  $candidates = @()
  if ($ExplicitPath) { $candidates += $ExplicitPath }
  $candidates += Join-Path $Root "tools\nssm\nssm.exe"
  $candidates += Join-Path $Root "bin\nssm.exe"
  $fromPath = Get-Command nssm.exe -ErrorAction SilentlyContinue
  if ($fromPath) { $candidates += $fromPath.Source }

  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
      return (Resolve-Path -LiteralPath $candidate).Path
    }
  }

  throw "nssm.exe not found. Put it in bin\nssm.exe or tools\nssm\nssm.exe, or pass -NssmPath."
}

function Get-VenvPython {
  param([string]$Root)
  $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "Python venv not found: $venvPython. Install dependencies first, or do not use -SkipPythonSync."
  }
  return $venvPython
}

function Get-DisplayPython {
  param([string]$Root)
  $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
  if (Test-Path -LiteralPath $pythonw) {
    return $pythonw
  }
  return (Get-VenvPython -Root $Root)
}

function Install-PythonEnvironment {
  param([string]$Root, [string]$ExplicitPython)

  $venvPath = Join-Path $Root ".venv"
  $venvPython = Join-Path $venvPath "Scripts\python.exe"

  if (Get-Command uv -ErrorAction SilentlyContinue) {
    if (-not (Test-Path -LiteralPath $venvPython)) {
      Invoke-Native uv venv $venvPath
    }
    $requirementsPath = Join-Path $env:TEMP "seat-aoi-runtime-requirements.txt"
    Invoke-Native uv export --format requirements.txt --frozen --no-hashes --no-emit-project --no-dev --extra onnx --extra faiss --extra display --output-file $requirementsPath
    Invoke-Native uv pip install --python $venvPython --requirement $requirementsPath
    Invoke-Native uv pip check --python $venvPython
    return
  }

  if (-not (Test-Path -LiteralPath $venvPython)) {
    if ($ExplicitPython) {
      Invoke-Native $ExplicitPython -m venv $venvPath
    } elseif (Get-Command py -ErrorAction SilentlyContinue) {
      Invoke-Native py -3.10 -m venv $venvPath
    } else {
      Invoke-Native python -m venv $venvPath
    }
  }

  Invoke-Native $venvPython -m pip install --upgrade pip
  Invoke-Native $venvPython -m pip install numpy PyYAML scipy onnxruntime faiss-cpu PySide6
  Invoke-Native $venvPython -m pip check
}

function Build-Controller {
  param(
    [string]$Root,
    [bool]$UseHikrobot,
    [string]$IncludeDir,
    [string]$LibraryPath
  )

  if (-not (Get-Command cmake -ErrorAction SilentlyContinue)) {
    throw "cmake not found. Install CMake, or build manually and place bin\seat_aoi_controller.exe."
  }

  $buildDir = Join-Path $Root "cpp_controller\build\station-release"
  $args = @(
    "-S", (Join-Path $Root "cpp_controller"),
    "-B", $buildDir,
    "-DCMAKE_BUILD_TYPE=Release"
  )

  if ($UseHikrobot) {
    if (-not (Test-Path -LiteralPath $IncludeDir)) {
      throw "Hikrobot MVS build is enabled, but include dir was not found: $IncludeDir"
    }
    if (-not (Test-Path -LiteralPath $LibraryPath)) {
      throw "Hikrobot MVS build is enabled, but library was not found: $LibraryPath"
    }
    $args += "-DSEAT_AOI_ENABLE_HIKROBOT_MVS=ON"
    $args += "-DSEAT_AOI_HIKROBOT_MVS_INCLUDE_DIR=$IncludeDir"
    $args += "-DSEAT_AOI_HIKROBOT_MVS_LIBRARY=$LibraryPath"
  }

  Invoke-Native cmake @args
  Invoke-Native cmake --build $buildDir --config Release

  $exe = Get-ChildItem -LiteralPath $buildDir -Recurse -Filter "seat_aoi_controller.exe" |
    Sort-Object FullName |
    Select-Object -First 1
  if ($null -eq $exe) {
    throw "C++ build finished, but seat_aoi_controller.exe was not found: $buildDir"
  }

  $binDir = Join-Path $Root "bin"
  New-Item -ItemType Directory -Force -Path $binDir | Out-Null
  Copy-Item -LiteralPath $exe.FullName -Destination (Join-Path $binDir "seat_aoi_controller.exe") -Force
}

function Wait-ServiceStopped {
  param([string]$Name, [int]$TimeoutSeconds = 30)
  $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
  while ((Get-Date) -lt $deadline) {
    $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
    if ($null -eq $service -or $service.Status -eq "Stopped") {
      return $true
    }
    Start-Sleep -Milliseconds 500
  }
  return $false
}

function Remove-ServiceIfExists {
  param([string]$Nssm, [string]$Name)
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($null -ne $service) {
    Invoke-NativeOptional $Nssm stop $Name
    if (-not (Wait-ServiceStopped -Name $Name)) {
      throw "Service did not stop within 30 seconds: $Name. Stop it manually before reinstalling."
    }
    Invoke-Native $Nssm remove $Name confirm
  }
}

function Install-NssmService {
  param(
    [string]$Nssm,
    [string]$Name,
    [string]$DisplayName,
    [string]$Description,
    [string]$Application,
    [string]$Arguments,
    [string]$Root,
    [string]$LogPrefix
  )

  Remove-ServiceIfExists -Nssm $Nssm -Name $Name

  $logDir = Join-Path $Root "logs\services"
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null

  Invoke-Native $Nssm install $Name $Application
  Invoke-Native $Nssm set $Name DisplayName $DisplayName
  Invoke-Native $Nssm set $Name Description $Description
  Invoke-Native $Nssm set $Name AppDirectory $Root
  Invoke-Native $Nssm set $Name AppParameters $Arguments
  Invoke-Native $Nssm set $Name Start SERVICE_AUTO_START
  Invoke-Native $Nssm set $Name AppRestartDelay 5000
  Invoke-Native $Nssm set $Name AppThrottle 1500
  Invoke-Native $Nssm set $Name AppStopMethodConsole 15000
  Invoke-Native $Nssm set $Name AppStdout (Join-Path $logDir "$LogPrefix.stdout.log")
  Invoke-Native $Nssm set $Name AppStderr (Join-Path $logDir "$LogPrefix.stderr.log")
  Invoke-Native $Nssm set $Name AppRotateFiles 1
  Invoke-Native $Nssm set $Name AppRotateOnline 1
  Invoke-Native $Nssm set $Name AppRotateBytes 10485760
  Invoke-Native $Nssm set $Name AppEnvironmentExtra "PYTHONUTF8=1`r`nPYTHONUNBUFFERED=1"
}

function Quote-ShortcutArgument {
  param([string]$Value)
  if ($Value -match '[\s"]') {
    return '"' + ($Value -replace '"', '\"') + '"'
  }
  return $Value
}

function Quote-ServiceArgument {
  param([string]$Value)
  if ($Value -match '[\s"]') {
    return '"' + ($Value -replace '"', '\"') + '"'
  }
  return $Value
}

function Initialize-DataDirectories {
  param([string]$DataRoot, [string]$ModelRoot)
  $dirs = @(
    (Join-Path $DataRoot "trace"),
    (Join-Path $DataRoot "images"),
    (Join-Path $DataRoot "logs"),
    (Join-Path $ModelRoot "roi_yolo"),
    (Join-Path $ModelRoot "wideresnet50"),
    (Join-Path $ModelRoot "patchcore")
  )
  foreach ($dir in $dirs) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
  }
  Write-Host "Data directories created under $DataRoot and $ModelRoot"
}

function Update-ProductionConfigPaths {
  param([string]$ConfigPath, [string]$DataRoot)
  $content = Get-Content -LiteralPath $ConfigPath -Encoding UTF8
  $updated = @()
  foreach ($line in $content) {
    if ($line -match '^\s*trace_root\s*=') {
      $updated += "trace_root=$(Join-Path $DataRoot 'trace')"
    } elseif ($line -match '^\s*image_save\.root_dir\s*=') {
      $updated += "image_save.root_dir=$(Join-Path $DataRoot 'images')"
    } else {
      $updated += $line
    }
  }
  [System.IO.File]::WriteAllLines($ConfigPath, $updated, [System.Text.UTF8Encoding]::new($false))
  Write-Host "Production config paths updated: trace_root/image_save.root_dir -> $DataRoot"
}

function Update-RecipeModelPaths {
  param([string]$RecipePath, [string]$ModelRoot, [string]$DataRoot)
  $content = Get-Content -LiteralPath $RecipePath -Encoding UTF8
  $updated = @()
  foreach ($line in $content) {
    if ($line -match '^\s*model_path:\s*model/roi_yolo/') {
      $updated += "  model_path: $(Join-Path $ModelRoot 'roi_yolo\seat_roi_seg.onnx')"
    } elseif ($line -match '^\s*embedding_model_path:\s*model/wideresnet50/') {
      $updated += "    embedding_model_path: $(Join-Path $ModelRoot 'wideresnet50\seat_wrn50_embedding.onnx')"
    } elseif ($line -match '^\s*pca_path:\s*model/patchcore/seat_pca\.json') {
      $updated += "    pca_path: $(Join-Path $ModelRoot 'patchcore\seat_pca.json')"
    } elseif ($line -match '^\s*memory_bank_path:\s*model/patchcore/seat_patchcore_bank\.json') {
      $updated += "    memory_bank_path: $(Join-Path $ModelRoot 'patchcore\seat_patchcore_bank.json')"
    } elseif ($line -match '^\s*faiss_index_path:\s*model/patchcore/seat_patchcore\.faiss') {
      $updated += "    faiss_index_path: $(Join-Path $ModelRoot 'patchcore\seat_patchcore.faiss')"
    } elseif ($line -match '^\s*root_dir:\s*trace\s*$') {
      $updated += "  root_dir: $(Join-Path $DataRoot 'trace')"
    } else {
      $updated += $line
    }
  }
  [System.IO.File]::WriteAllLines($RecipePath, $updated, [System.Text.UTF8Encoding]::new($false))
  Write-Host "Recipe model paths updated: -> $ModelRoot, trace -> $DataRoot"
}

function Copy-ModelAssets {
  param([string]$Root, [string]$ModelRoot)
  $modelDir = Join-Path $Root "model"
  if (-not (Test-Path -LiteralPath $modelDir)) {
    Write-Host "Model source directory not found, skip model copy: $modelDir"
    return
  }
  $copies = @(
    @{Src="roi_yolo\seat_roi_seg.onnx"; Dst="roi_yolo\seat_roi_seg.onnx"},
    @{Src="wideresnet50\seat_wrn50_embedding.onnx"; Dst="wideresnet50\seat_wrn50_embedding.onnx"},
    @{Src="patchcore\seat_pca.json"; Dst="patchcore\seat_pca.json"},
    @{Src="patchcore\seat_patchcore_bank.json"; Dst="patchcore\seat_patchcore_bank.json"},
    @{Src="patchcore\seat_patchcore_bank.npy"; Dst="patchcore\seat_patchcore_bank.npy"},
    @{Src="patchcore\seat_patchcore.faiss"; Dst="patchcore\seat_patchcore.faiss"}
  )
  foreach ($copy in $copies) {
    $src = Join-Path $modelDir $copy.Src
    $dst = Join-Path $ModelRoot $copy.Dst
    if (Test-Path -LiteralPath $src) {
      Copy-Item -LiteralPath $src -Destination $dst -Force
      Write-Host "Model copied: $src -> $dst"
    } else {
      Write-Host "Model not found (placeholder?), skip: $src"
    }
  }
}

function Remove-PythonSources {
  param([string]$Root)
  Write-Host "Cleaning Python source files..."
  $pyDirs = @(
    (Join-Path $Root "python_detector"),
    (Join-Path $Root "display_app"),
    (Join-Path $Root "tools"),
    (Join-Path $Root "training_tools")
  )
  foreach ($dir in $pyDirs) {
    if (-not (Test-Path -LiteralPath $dir)) { continue }
    Get-ChildItem -LiteralPath $dir -Recurse -Filter "*.py" | ForEach-Object {
      Remove-Item -LiteralPath $_.FullName -Force
    }
  }
  Write-Host "Python source files removed."
}

function New-DisplayShortcut {
  param(
    [string]$Root,
    [string]$TargetPath,
    [string]$Name,
    [string]$Trace,
    [string]$Line,
    [string]$Grid,
    [bool]$EnableManualTrigger,
    [string]$ManualTriggerHost,
    [int]$ManualTriggerPort,
    [int]$ManualTriggerTimeoutMs,
    [bool]$UseCurrentUserDesktop,
    [bool]$CreateStartup
  )

  if ($UseCurrentUserDesktop) {
    $desktop = [Environment]::GetFolderPath("Desktop")
  } else {
    $desktop = Join-Path $env:PUBLIC "Desktop"
  }
  if (-not (Test-Path -LiteralPath $desktop)) {
    $desktop = [Environment]::GetFolderPath("Desktop")
  }

  # Determine if this is a packaged .exe (path does not contain "python")
  $targetName = [IO.Path]::GetFileName($TargetPath)
  $isPackagedExe = $targetName -notmatch 'python'

  if ($isPackagedExe) {
    $argumentParts = @(
      "--trace-root",
      (Quote-ShortcutArgument $Trace),
      "--line-id",
      (Quote-ShortcutArgument $Line),
      "--grid-layout",
      (Quote-ShortcutArgument $Grid)
    )
  } else {
    $argumentParts = @(
      "-m",
      "display_app.main",
      "--trace-root",
      (Quote-ShortcutArgument $Trace),
      "--line-id",
      (Quote-ShortcutArgument $Line),
      "--grid-layout",
      (Quote-ShortcutArgument $Grid)
    )
  }
  if ($EnableManualTrigger) {
    $argumentParts += @(
      "--enable-manual-trigger",
      "--manual-trigger-host",
      (Quote-ShortcutArgument $ManualTriggerHost),
      "--manual-trigger-port",
      "$ManualTriggerPort",
      "--manual-trigger-timeout-ms",
      "$ManualTriggerTimeoutMs"
    )
  }

  $shortcutPath = Join-Path $desktop "$Name.lnk"
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut($shortcutPath)
  $shortcut.TargetPath = $TargetPath
  $shortcut.Arguments = ($argumentParts -join " ")
  $shortcut.WorkingDirectory = $Root
  $shortcut.Description = "Seat Surface AOI display app"
  $shortcut.IconLocation = "$TargetPath,0"
  $shortcut.Save()

  if ($CreateStartup) {
    $startup = [Environment]::GetFolderPath("Startup")
    $startupShortcutPath = Join-Path $startup "$Name.lnk"
    Copy-Item -LiteralPath $shortcutPath -Destination $startupShortcutPath -Force
  }

  return $shortcutPath
}

if (-not (Test-IsAdministrator)) {
  throw "Administrator PowerShell is required to install Windows services."
}

$ProjectRoot = Resolve-ProjectRoot -Value $ProjectRoot
$ConfigFullPath = if ([IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $ProjectRoot $ConfigPath }
$ControllerExe = Join-Path $ProjectRoot "bin\seat_aoi_controller.exe"

Push-Location $ProjectRoot
try {
  $Nssm = Resolve-Nssm -ExplicitPath $NssmPath -Root $ProjectRoot
  Remove-ServiceIfExists -Nssm $Nssm -Name $DetectorServiceName
  Remove-ServiceIfExists -Nssm $Nssm -Name $ControllerServiceName

  if (-not $SkipPythonSync) {
    Install-PythonEnvironment -Root $ProjectRoot -ExplicitPython $PythonExe
  }
  $VenvPython = Get-VenvPython -Root $ProjectRoot
  $DisplayPython = Get-DisplayPython -Root $ProjectRoot

  if ($BuildController) {
    Build-Controller -Root $ProjectRoot -UseHikrobot ([bool]$EnableHikrobotMvs) -IncludeDir $HikrobotIncludeDir -LibraryPath $HikrobotLibrary
  }
  if (-not (Test-Path -LiteralPath $ControllerExe)) {
    throw "C++ controller not found: $ControllerExe. Build it first, or rerun with -BuildController."
  }
  if (-not (Test-Path -LiteralPath $ConfigFullPath)) {
    throw "Production config not found: $ConfigFullPath"
  }

  # ---- PyInstaller: build Python packages ----
  $DetectorExe = Join-Path $ProjectRoot "bin\seat_aoi_detector.exe"
  $DisplayDir = Join-Path $ProjectRoot "bin\seat_aoi_display"
  $DisplayExe = Join-Path $DisplayDir "seat_aoi_display.exe"
  if ($BuildPythonPackages) {
    $pyinstallerScript = Join-Path $ProjectRoot "tools\windows\build_python_packages.ps1"
    $pyinstallerArgs = @{
      ProjectRoot = $ProjectRoot
      PythonExe = $VenvPython
      PyinstallerKey = $PyinstallerKey
      CleanBuild = $false
    }
    & $pyinstallerScript @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
      throw "PyInstaller build failed. Check that VC++ Build Tools are installed."
    }
  }

  # ---- D: drive data dirs and path injection ----
  if (Test-Path -LiteralPath $DataRoot) { } else {
    New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
  }
  if (Test-Path -LiteralPath $ModelRoot) { } else {
    New-Item -ItemType Directory -Force -Path $ModelRoot | Out-Null
  }
  Initialize-DataDirectories -DataRoot $DataRoot -ModelRoot $ModelRoot

  Update-ProductionConfigPaths -ConfigPath $ConfigFullPath -DataRoot $DataRoot

  $recipeDir = Join-Path $ProjectRoot "python_detector\config"
  $recipePath = Join-Path $recipeDir "$Recipe.yaml"
  if (Test-Path -LiteralPath $recipePath) {
    Update-RecipeModelPaths -RecipePath $recipePath -ModelRoot $ModelRoot -DataRoot $DataRoot
  } else {
    Write-Host "Recipe not found, skip path update: $recipePath"
  }

  Copy-ModelAssets -Root $ProjectRoot -ModelRoot $ModelRoot

  # ---- Determine service and display entry points ----
  $DetectorApp = $VenvPython
  $DetectorArgs = "-m python_detector.detector_main --config $(Quote-ServiceArgument $ConfigPath)"
  $DisplayApp = $DisplayPython
  $DisplayExtraArgs = @()

  if ($BuildPythonPackages -and (Test-Path -LiteralPath $DetectorExe)) {
    $DetectorApp = $DetectorExe
    $DetectorArgs = "--config $(Quote-ServiceArgument $ConfigPath) --recipe-dir $(Quote-ServiceArgument $recipeDir)"
  }
  if ($BuildPythonPackages -and (Test-Path -LiteralPath $DisplayExe)) {
    $DisplayApp = $DisplayExe
    # display .exe bundles QML internally, no -m display_app.main needed
    $DisplayExtraArgs = @()
  }

  if (-not $SkipValidation) {
    Invoke-Native $ControllerExe --config $ConfigPath --validate-config
    Invoke-Native $VenvPython -m tools.validate_protocol
    Invoke-Native $VenvPython -m tools.validate_model_assets --recipe $Recipe
  }

  Install-NssmService `
    -Nssm $Nssm `
    -Name $DetectorServiceName `
    -DisplayName "Seat AOI Python Detector" `
    -Description "Seat Surface AOI Python detector process." `
    -Application $DetectorApp `
    -Arguments $DetectorArgs `
    -Root $ProjectRoot `
    -LogPrefix "detector"

  Install-NssmService `
    -Nssm $Nssm `
    -Name $ControllerServiceName `
    -DisplayName "Seat AOI C++ Controller" `
    -Description "Seat Surface AOI C++ controller process." `
    -Application $ControllerExe `
    -Arguments "--config $(Quote-ServiceArgument $ConfigPath) --loop" `
    -Root $ProjectRoot `
    -LogPrefix "controller"
  Invoke-Native $Nssm set $DetectorServiceName DependOnService $ControllerServiceName

  $shortcutPath = New-DisplayShortcut `
    -Root $ProjectRoot `
    -TargetPath $DisplayApp `
    -Name $ShortcutName `
    -Trace (Join-Path $DataRoot 'trace') `
    -Line $LineId `
    -Grid $GridLayout `
    -EnableManualTrigger ([bool]$EnableDisplayManualTrigger) `
    -ManualTriggerHost $ManualTriggerHost `
    -ManualTriggerPort $ManualTriggerPort `
    -ManualTriggerTimeoutMs $ManualTriggerTimeoutMs `
    -UseCurrentUserDesktop ([bool]$CurrentUserShortcut) `
    -CreateStartup ([bool]$CreateStartupShortcut)

  if (-not $NoStartServices) {
    Invoke-Native $Nssm start $ControllerServiceName
    Start-Sleep -Seconds 3
    Invoke-Native $Nssm start $DetectorServiceName
  }

  # ---- Clean Python sources ----
  if ($CleanPythonSource) {
    if ($BuildPythonPackages) {
      Remove-PythonSources -Root $ProjectRoot
    } else {
      Write-Host "WARNING: -CleanPythonSource requires -BuildPythonPackages. Skipping source cleanup."
    }
  }

  Write-Host "Seat Surface AOI station installation completed."
  Write-Host "ProjectRoot: $ProjectRoot"
  Write-Host "DataRoot  : $DataRoot"
  Write-Host "ModelRoot : $ModelRoot"
  Write-Host "Detector service: $DetectorServiceName"
  Write-Host "Controller service: $ControllerServiceName"
  Write-Host "Display shortcut: $shortcutPath"
} finally {
  Pop-Location
}
