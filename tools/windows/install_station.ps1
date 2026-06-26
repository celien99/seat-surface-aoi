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
  [switch]$CreateStartupShortcut
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
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
  & $Command[0] @($Command | Select-Object -Skip 1)
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed, exit=${LASTEXITCODE}: $($Command -join ' ')"
  }
}

function Invoke-NativeOptional {
  param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Command)
  & $Command[0] @($Command | Select-Object -Skip 1)
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

function Remove-ServiceIfExists {
  param([string]$Nssm, [string]$Name)
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($null -ne $service) {
    Invoke-NativeOptional $Nssm stop $Name
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

function New-DisplayShortcut {
  param(
    [string]$Root,
    [string]$TargetPath,
    [string]$Name,
    [string]$Trace,
    [string]$Line,
    [string]$Grid,
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

  if (-not $SkipValidation) {
    Invoke-Native $ControllerExe --config $ConfigPath --validate-config
    Invoke-Native $VenvPython -m tools.validate_protocol
    Invoke-Native $VenvPython -m tools.validate_model_assets --recipe $Recipe
  }

  $Nssm = Resolve-Nssm -ExplicitPath $NssmPath -Root $ProjectRoot
  Install-NssmService `
    -Nssm $Nssm `
    -Name $DetectorServiceName `
    -DisplayName "Seat AOI Python Detector" `
    -Description "Seat Surface AOI Python detector process." `
    -Application $VenvPython `
    -Arguments "-m python_detector.detector_main --config $(Quote-ServiceArgument $ConfigPath)" `
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
    -TargetPath $DisplayPython `
    -Name $ShortcutName `
    -Trace $TraceRoot `
    -Line $LineId `
    -Grid $GridLayout `
    -UseCurrentUserDesktop ([bool]$CurrentUserShortcut) `
    -CreateStartup ([bool]$CreateStartupShortcut)

  if (-not $NoStartServices) {
    Invoke-Native $Nssm start $ControllerServiceName
    Start-Sleep -Seconds 3
    Invoke-Native $Nssm start $DetectorServiceName
  }

  Write-Host "Seat Surface AOI station installation completed."
  Write-Host "ProjectRoot: $ProjectRoot"
  Write-Host "Detector service: $DetectorServiceName"
  Write-Host "Controller service: $ControllerServiceName"
  Write-Host "Display shortcut: $shortcutPath"
} finally {
  Pop-Location
}
