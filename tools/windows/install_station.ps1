param(
  [string]$ProjectRoot = "",
  [string]$ConfigPath = "cpp_controller\config\station_runtime.production.conf",
  [string]$TraceRoot = "trace",
  [string]$LineId = "LINE1_AOI_01",
  [ValidatePattern("^\d+x\d+$")]
  [string]$GridLayout = "2x1",
  [string]$Recipe = "production_recipe",
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
  [string]$DataRoot = "",
  [string]$ModelRoot = "",
  [switch]$CleanPythonSource
)

$ErrorActionPreference = "Stop"

Import-Module -Force "$PSScriptRoot\module\SeatAoiDeployment\SeatAoiDeployment.psd1" -WarningAction SilentlyContinue

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

  Invoke-Native -ArgList (@("cmake") + $args)
  Invoke-Native -ArgList @("cmake", "--build", $buildDir, "--config", "Release")

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

function Install-NssmService {
  param(
    [string]$Nssm,
    [string]$Name,
    [string]$DisplayName,
    [string]$Description,
    [string]$Application,
    [string]$Arguments,
    [string]$Root,
    [string]$ServiceLogRoot,
    [string]$LogPrefix,
    [switch]$NoPythonEnv
  )

  Remove-ServiceIfExists -Nssm $Nssm -Name $Name

  if ($ServiceLogRoot) {
    $logDir = $ServiceLogRoot
  } else {
    $logDir = Join-Path $Root "logs\services"
  }
  New-Item -ItemType Directory -Force -Path $logDir | Out-Null

  Invoke-Native -ArgList @($Nssm, "install", $Name, $Application)
  Invoke-Native -ArgList @($Nssm, "set", $Name, "DisplayName", $DisplayName)
  Invoke-Native -ArgList @($Nssm, "set", $Name, "Description", $Description)
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppDirectory", $Root)
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppParameters", $Arguments)
  Invoke-Native -ArgList @($Nssm, "set", $Name, "Start", "SERVICE_AUTO_START")
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppRestartDelay", "5000")
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppThrottle", "1500")
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppStopMethodConsole", "15000")
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppStdout", (Join-Path $logDir "$LogPrefix.stdout.log"))
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppStderr", (Join-Path $logDir "$LogPrefix.stderr.log"))
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppRotateFiles", "1")
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppRotateOnline", "1")
  Invoke-Native -ArgList @($Nssm, "set", $Name, "AppRotateBytes", "10485760")
  if (-not $NoPythonEnv) {
    Invoke-Native -ArgList @($Nssm, "set", $Name, "AppEnvironmentExtra", "PYTHONUTF8=1`r`nPYTHONUNBUFFERED=1")
  }
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

function Convert-RecipeAssetPath {
  param([string]$Value, [string]$ModelRoot)
  $clean = $Value.Trim().Trim('"', "'")
  $segments = @($clean -split '[\\/]+') | Where-Object { $_ -and $_ -ne "." }
  $knownRoots = @("roi_yolo", "wideresnet50", "patchcore")
  $suffixSegments = @()
  for ($index = 0; $index -lt $segments.Count; $index++) {
    if ($segments[$index] -eq "model") {
      if ($index + 1 -ge $segments.Count) {
        break
      }
      $suffixSegments = @($segments[($index + 1)..($segments.Count - 1)])
      break
    }
    if ($knownRoots -contains $segments[$index]) {
      $suffixSegments = @($segments[$index..($segments.Count - 1)])
      break
    }
  }
  if ($suffixSegments.Count -eq 0) {
    throw "Cannot map recipe asset path to ModelRoot: $Value. Use model/<subdir>/<file> or a known model subdir."
  }
  $resolved = $ModelRoot
  foreach ($segment in $suffixSegments) {
    $resolved = Join-Path $resolved $segment
  }
  return $resolved
}

function Update-RecipeModelPaths {
  param([string]$RecipePath, [string]$ModelRoot, [string]$DataRoot)
  $content = Get-Content -LiteralPath $RecipePath -Encoding UTF8
  $updated = @()
  foreach ($line in $content) {
    if ($line -match '^(\s*)(model_path|embedding_model_path|pca_path|memory_bank_path|faiss_index_path)\s*:\s*(.+?)\s*(?:#.*)?$') {
      $indent = $matches[1]
      $key = $matches[2]
      $assetPath = Convert-RecipeAssetPath -Value $matches[3] -ModelRoot $ModelRoot
      $updated += "$indent${key}: $assetPath"
    } elseif ($line -match '^(\s*)root_dir\s*:\s*.*$') {
      $updated += "$($matches[1])root_dir: $(Join-Path $DataRoot 'trace')"
    } else {
      $updated += $line
    }
  }
  [System.IO.File]::WriteAllLines($RecipePath, $updated, [System.Text.UTF8Encoding]::new($false))
  Write-Host "Recipe model paths updated: -> $ModelRoot, trace -> $DataRoot"
}

function Get-StationConfigValue {
  param([string]$ConfigPath, [string]$Key)
  foreach ($line in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
    $clean = ($line -split '#', 2)[0].Trim()
    if (-not $clean -or -not $clean.Contains("=")) {
      continue
    }
    $parts = $clean.Split("=", 2)
    if ($parts[0].Trim() -eq $Key) {
      return $parts[1].Trim()
    }
  }
  return ""
}

function Get-RecipeIdFromYaml {
  param([string]$RecipePath)
  foreach ($line in Get-Content -LiteralPath $RecipePath -Encoding UTF8) {
    $clean = ($line -split '#', 2)[0].Trim()
    if ($clean -match '^recipe_id\s*:\s*(.+)$') {
      return $matches[1].Trim().Trim('"', "'")
    }
  }
  return ""
}

function Resolve-ActiveRecipePath {
  param([string]$RecipeDir, [string]$RecipeArg, [string]$RecipeId)
  $candidates = @()
  if ($RecipeArg) {
    $argPath = if ([IO.Path]::IsPathRooted($RecipeArg)) { $RecipeArg } else { Join-Path $RecipeDir $RecipeArg }
    if ([IO.Path]::GetExtension($argPath) -eq "") {
      $argPath = "$argPath.yaml"
    }
    if (Test-Path -LiteralPath $argPath) {
      $candidates += (Resolve-Path -LiteralPath $argPath).Path
    }
  }
  foreach ($candidate in Get-ChildItem -LiteralPath $RecipeDir -Filter "*.yaml") {
    if ($candidate.Name.EndsWith(".example.yaml")) {
      continue
    }
    $candidateRecipeId = Get-RecipeIdFromYaml -RecipePath $candidate.FullName
    if ($candidateRecipeId -eq $RecipeId) {
      $candidates += $candidate.FullName
    }
  }
  $unique = @($candidates | Select-Object -Unique)
  if ($unique.Count -eq 0) {
    throw "Active recipe YAML not found for recipe_id=$RecipeId under $RecipeDir. Do not continue with unpatched recipe paths."
  }
  if ($unique.Count -gt 1) {
    throw "Multiple recipe YAML files matched recipe_id=$RecipeId or -Recipe=${RecipeArg}: $($unique -join ', ')"
  }
  $resolved = $unique[0]
  $actualRecipeId = Get-RecipeIdFromYaml -RecipePath $resolved
  if ($actualRecipeId -ne $RecipeId) {
    throw "-Recipe points to $resolved with recipe_id=$actualRecipeId, but production config uses recipe_id=$RecipeId"
  }
  return $resolved
}

function Assert-RecipeDeploymentPaths {
  param([string]$RecipePath, [string]$ModelRoot, [string]$DataRoot)
  $content = Get-Content -LiteralPath $RecipePath -Encoding UTF8
  $modelRootPrefix = ([System.IO.Path]::GetFullPath($ModelRoot)).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
  $traceRoot = [System.IO.Path]::GetFullPath((Join-Path $DataRoot 'trace'))
  $pathKeys = @("model_path", "embedding_model_path", "pca_path", "memory_bank_path", "faiss_index_path")
  foreach ($line in $content) {
    foreach ($key in $pathKeys) {
      if ($line -match "^\s*$key\s*:\s*(.+?)\s*(?:#.*)?$") {
        $value = $matches[1].Trim().Trim('"', "'")
        if ($value -notmatch '^(?:[A-Za-z]:[\\/]|\\\\)') {
          throw "Recipe path is not absolute after injection: $RecipePath $key=$value"
        }
        $fullValue = [System.IO.Path]::GetFullPath($value)
        if (-not $fullValue.StartsWith($modelRootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
          throw "Recipe path is outside ModelRoot after injection: $RecipePath $key=$value ModelRoot=$ModelRoot"
        }
      }
    }
    if ($line -match '^\s*root_dir\s*:\s*(.+?)\s*(?:#.*)?$') {
      $value = $matches[1].Trim().Trim('"', "'")
      if ([System.IO.Path]::GetFullPath($value) -ne $traceRoot) {
        throw "Recipe trace root mismatch after injection: $RecipePath root_dir=$value expected=$traceRoot"
      }
    }
  }
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
      if (Test-PlaceholderFile -Path $src) {
        Write-Host "Model source is placeholder, skip: $src"
        continue
      }
      New-Item -ItemType Directory -Force -Path (Split-Path -Parent $dst) | Out-Null
      Copy-Item -LiteralPath $src -Destination $dst -Force
      Write-Host "Model copied: $src -> $dst"
    } else {
      Write-Host "Model not found (placeholder?), skip: $src"
    }
  }
}

function Remove-PythonSources {
  param([string]$Root)
  Write-Host "[WARNING] Removing Python source files. Rebuild will REQUIRE source file restoration first."
  Write-Host "[WARNING] To restore before rebuilding, re-clone or restore python_detector/ display_app/ tools/ training_tools/ from backup."
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
      (Quote-Argument $Trace),
      "--line-id",
      (Quote-Argument $Line),
      "--grid-layout",
      (Quote-Argument $Grid)
    )
  } else {
    $argumentParts = @(
      "-m",
      "display_app.main",
      "--trace-root",
      (Quote-Argument $Trace),
      "--line-id",
      (Quote-Argument $Line),
      "--grid-layout",
      (Quote-Argument $Grid)
    )
  }
  if ($EnableManualTrigger) {
    $argumentParts += @(
      "--enable-manual-trigger",
      "--manual-trigger-host",
      (Quote-Argument $ManualTriggerHost),
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
$DataRoot = Resolve-DeploymentRoot -Value $DataRoot -DefaultLeafName "seat-aoi-data" -Root $ProjectRoot
$ModelRoot = Resolve-DeploymentRoot -Value $ModelRoot -DefaultLeafName "seat-aoi-model" -Root $ProjectRoot
$ConfigFullPath = if ([IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else { Join-Path $ProjectRoot $ConfigPath }
if (-not (Test-Path -LiteralPath $ConfigFullPath)) {
  throw "Production config not found: $ConfigFullPath"
}
$recipeDir = Join-Path $ProjectRoot "python_detector\config"
if (-not (Test-Path -LiteralPath $recipeDir)) {
  throw "Recipe config directory not found: $recipeDir. Source files may have been deleted by -CleanPythonSource. Restore source files before reinstalling."
}
$ControllerExe = Join-Path $ProjectRoot "bin\seat_aoi_controller.exe"

Push-Location $ProjectRoot
try {
  $Nssm = Resolve-Nssm -ExplicitPath $NssmPath -Root $ProjectRoot
  Remove-ServiceIfExists -Nssm $Nssm -Name $DetectorServiceName
  Remove-ServiceIfExists -Nssm $Nssm -Name $ControllerServiceName

  $VenvPython = Get-VenvPython -Root $ProjectRoot
  if (-not $SkipPythonSync) {
    Assert-PythonVersionSupported -PythonPath $VenvPython
    Assert-PythonModulesAvailable `
      -PythonPath $VenvPython `
      -Modules @("yaml", "numpy", "scipy", "onnxruntime", "faiss", "cv2", "PySide6") `
      -InstallHint "Run uv sync first, or do not use -SkipPythonSync."
  }
  $DisplayPython = Get-DisplayPython -Root $ProjectRoot

  if ($BuildController) {
    Build-Controller -Root $ProjectRoot -UseHikrobot ([bool]$EnableHikrobotMvs) -IncludeDir $HikrobotIncludeDir -LibraryPath $HikrobotLibrary
  }
  if (-not (Test-Path -LiteralPath $ControllerExe)) {
    throw "C++ controller not found: $ControllerExe. Build it first, or rerun with -BuildController."
  }

  $DetectorExe = Join-Path $ProjectRoot "bin\seat_aoi_detector.exe"
  $DisplayDir = Join-Path $ProjectRoot "bin\seat_aoi_display"
  $DisplayExe = Join-Path $DisplayDir "seat_aoi_display.exe"

  # ---- Data/model dirs and path injection ----
  if (Test-Path -LiteralPath $DataRoot) { } else {
    New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
  }
  if (Test-Path -LiteralPath $ModelRoot) { } else {
    New-Item -ItemType Directory -Force -Path $ModelRoot | Out-Null
  }
  Initialize-DataDirectories -DataRoot $DataRoot -ModelRoot $ModelRoot
  $ServiceLogRoot = Join-Path $DataRoot "logs\services"

  Update-ProductionConfigPaths -ConfigPath $ConfigFullPath -DataRoot $DataRoot

  $ActiveRecipeId = Get-StationConfigValue -ConfigPath $ConfigFullPath -Key "recipe_id"
  if (-not $ActiveRecipeId) {
    throw "recipe_id not found in production config: $ConfigFullPath"
  }
  $recipePath = Resolve-ActiveRecipePath -RecipeDir $recipeDir -RecipeArg $Recipe -RecipeId $ActiveRecipeId
  Update-RecipeModelPaths -RecipePath $recipePath -ModelRoot $ModelRoot -DataRoot $DataRoot
  Assert-RecipeDeploymentPaths -RecipePath $recipePath -ModelRoot $ModelRoot -DataRoot $DataRoot

  Copy-ModelAssets -Root $ProjectRoot -ModelRoot $ModelRoot

  # ---- PyInstaller: build Python packages after config path injection ----
  if ($BuildPythonPackages) {
    $pyiCheck = Invoke-NativeQuiet @($VenvPython, "-c", "import PyInstaller")
    if ($pyiCheck -ne 0) {
      Invoke-Native -ArgList @("uv", "pip", "install", "--python", $VenvPython, "pyinstaller>=6.0")
    }
    $pyinstallerScript = Join-Path $ProjectRoot "tools\windows\build_python_packages.ps1"
    $pyinstallerArgs = @{
      ProjectRoot = $ProjectRoot
      PythonExe = $VenvPython
      CleanBuild = $false
    }
    & $pyinstallerScript @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
      throw "PyInstaller build failed. Check that VC++ Build Tools are installed."
    }
  }

  # ---- Determine service and display entry points ----
  $DetectorApp = $VenvPython
  $DetectorArgs = "-m python_detector.detector_main --config $(Quote-Argument $ConfigPath) --recipe-dir $(Quote-Argument $recipeDir)"
  $DisplayApp = $DisplayPython

  if ($BuildPythonPackages -and (Test-Path -LiteralPath $DetectorExe)) {
    $DetectorApp = $DetectorExe
    $DetectorArgs = "--config $(Quote-Argument $ConfigPath) --recipe-dir $(Quote-Argument $recipeDir)"
  }
  if ($BuildPythonPackages -and (Test-Path -LiteralPath $DisplayExe)) {
    $DisplayApp = $DisplayExe
  }

  if (-not $SkipValidation) {
    Invoke-Native -ArgList @($ControllerExe, "--config", $ConfigPath, "--validate-config")
    Invoke-Native -ArgList @($VenvPython, "-m", "tools.validate_protocol")
    if ($BuildPythonPackages -and (Test-Path -LiteralPath $DetectorExe)) {
      Invoke-Native -ArgList @($DetectorExe, "--config", $ConfigPath, "--recipe-dir", $recipeDir, "--validate-config-only")
    } else {
      Invoke-Native -ArgList @($VenvPython, "-m", "python_detector.detector_main", "--config", $ConfigPath, "--recipe-dir", $recipeDir, "--validate-config-only")
    }
    Invoke-Native -ArgList @($VenvPython, "-m", "tools.validate_model_assets", "--recipe", $recipePath)
  }

  Install-NssmService `
    -Nssm $Nssm `
    -Name $DetectorServiceName `
    -DisplayName "Seat AOI Python Detector" `
    -Description "Seat Surface AOI Python detector process." `
    -Application $DetectorApp `
    -Arguments $DetectorArgs `
    -Root $ProjectRoot `
    -ServiceLogRoot $ServiceLogRoot `
    -LogPrefix "detector"

  Install-NssmService `
    -Nssm $Nssm `
    -Name $ControllerServiceName `
    -DisplayName "Seat AOI C++ Controller" `
    -Description "Seat Surface AOI C++ controller process." `
    -Application $ControllerExe `
    -Arguments "--config $(Quote-Argument $ConfigPath) --loop" `
    -Root $ProjectRoot `
    -ServiceLogRoot $ServiceLogRoot `
    -LogPrefix "controller" `
    -NoPythonEnv
  Invoke-Native -ArgList @($Nssm, "set", $DetectorServiceName, "DependOnService", $ControllerServiceName)

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
    Invoke-Native -ArgList @($Nssm, "start", $ControllerServiceName)
    Start-Sleep -Seconds 3
    Invoke-Native -ArgList @($Nssm, "start", $DetectorServiceName)
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
  Write-Host "Service logs: $ServiceLogRoot"
  Write-Host "Display manual trigger: $([bool]$EnableDisplayManualTrigger)"
  Write-Host "Detector service: $DetectorServiceName"
  Write-Host "Controller service: $ControllerServiceName"
  Write-Host "Display shortcut: $shortcutPath"
} finally {
  Pop-Location
}
