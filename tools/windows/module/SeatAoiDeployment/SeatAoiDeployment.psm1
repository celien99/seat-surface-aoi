# SeatAoiDeployment.psm1
# Shared utilities for Windows deployment scripts. PowerShell 5.1, ASCII-only.
Set-StrictMode -Version Latest

# ============================================================================
# System checks
# ============================================================================

function Test-IsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Assert-PythonVersionSupported {
  param([string]$PythonPath)
  $versionText = (& $PythonPath -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
  if (-not ($versionText -match '^(\d+)\.(\d+)$')) {
    throw "Cannot read Python version: $PythonPath"
  }
  $major = [int]$matches[1]
  $minor = [int]$matches[2]
  if ($major -ne 3 -or $minor -lt 10 -or $minor -gt 12) {
    throw "Unsupported Python version $versionText. Use Python 3.10-3.12; onnxruntime/faiss/opencv delivery wheels are validated for this range."
  }
}

# ============================================================================
# Path resolution
# ============================================================================

function Resolve-ProjectRoot {
  param([string]$Value)
  if ($Value) {
    return (Resolve-Path -LiteralPath $Value).Path
  }
  return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..\..")).Path
}

function Resolve-DefaultRootOnProjectDrive {
  param([string]$Root, [string]$LeafName)
  $rootPath = [System.IO.Path]::GetPathRoot($Root)
  if (-not $rootPath) {
    throw "Cannot determine project drive from ProjectRoot: $Root"
  }
  return Join-Path $rootPath $LeafName
}

function Resolve-DeploymentRoot {
  param([string]$Value, [string]$DefaultLeafName, [string]$Root)
  if (-not $Value) {
    return (Resolve-DefaultRootOnProjectDrive -Root $Root -LeafName $DefaultLeafName)
  }
  $projectDrive = [System.IO.Path]::GetPathRoot($Root)
  if ($Value -match '^[A-Za-z]:[^\\/]') {
    throw "Deployment root must be fully qualified or relative to ProjectRoot, not drive-relative: $Value"
  }
  if ($Value -match '^[\\/][^\\/]') {
    $rootRelative = $Value -replace '^[\\/]+', ''
    return [System.IO.Path]::GetFullPath((Join-Path $projectDrive $rootRelative))
  }
  if ($Value -match '^(?:[A-Za-z]:[\\/]|\\\\)') {
    return [System.IO.Path]::GetFullPath($Value)
  }
  return [System.IO.Path]::GetFullPath((Join-Path $Root $Value))
}

# ============================================================================
# Native command execution
# ============================================================================

function Invoke-Native {
  param([string[]]$ArgList)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $ArgList[0] @($ArgList | Select-Object -Skip 1) 2>&1 | ForEach-Object { Write-Host $_ }
    $exitCode = $LASTEXITCODE
    if ($exitCode -ne 0) {
      throw "Command failed, exit=${exitCode}: $($ArgList -join ' ')"
    }
  } finally {
    $ErrorActionPreference = $saved
  }
}

function Invoke-NativeOptional {
  param([string[]]$ArgList)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $ArgList[0] @($ArgList | Select-Object -Skip 1) 2>&1 | Out-Null
  } finally {
    $ErrorActionPreference = $saved
  }
}

function Invoke-NativeQuiet {
  param([string[]]$ArgList)
  $saved = $ErrorActionPreference
  $ErrorActionPreference = "Continue"
  try {
    & $ArgList[0] @($ArgList | Select-Object -Skip 1) 2>&1 | Out-Null
    return $LASTEXITCODE
  } finally {
    $ErrorActionPreference = $saved
  }
}

# ============================================================================
# Python environment
# ============================================================================

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

function Get-DisplayPython {
  param([string]$Root)
  $pythonw = Join-Path $Root ".venv\Scripts\pythonw.exe"
  if (Test-Path -LiteralPath $pythonw) {
    return $pythonw
  }
  return (Get-VenvPython -Root $Root)
}

function Assert-PythonModulesAvailable {
  param(
    [string]$PythonPath,
    [string[]]$Modules,
    [string]$InstallHint
  )
  $moduleList = ($Modules -join ",")
  $probe = @"
import importlib, sys
missing = []
for name in '$moduleList'.split(','):
    try: importlib.import_module(name)
    except Exception as exc: missing.append(f'{name} ({type(exc).__name__}: {exc})')
if missing:
    print('Missing or unloadable Python modules: ' + '; '.join(missing), file=sys.stderr)
    sys.exit(1)
"@
  try {
    Invoke-Native -ArgList @($PythonPath, "-c", $probe)
  } catch {
    throw "Python runtime dependencies are incomplete. $InstallHint"
  }
}

# ============================================================================
# Package index arguments
# ============================================================================

function Get-UvPackageIndexArguments {
  param(
    [string]$IndexUrl,
    [string]$FindLinks,
    [bool]$NoIndex
  )
  $extra = @()
  if ($IndexUrl) {
    $extra += @("--default-index", $IndexUrl)
  }
  if ($FindLinks) {
    $extra += @("--find-links", $FindLinks)
  }
  if ($NoIndex) {
    $extra += "--no-index"
  }
  return $extra
}

# ============================================================================
# Service management
# ============================================================================

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
  param(
    [string]$Nssm,
    [string]$Name,
    [string]$Context = "reinstall"
  )
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($null -ne $service) {
    Invoke-NativeOptional -ArgList @($Nssm, "stop", $Name)
    if (-not (Wait-ServiceStopped -Name $Name)) {
      throw "Service did not stop within 30 seconds: $Name. Stop it manually before $Context."
    }
    Invoke-Native -ArgList @($Nssm, "remove", $Name, "confirm")
    Write-Host "Removed service: $Name"
  } else {
    Write-Host "Service not found, skipped: $Name"
  }
}

# ============================================================================
# Utilities
# ============================================================================

function Quote-Argument {
  param([string]$Value)
  if ($Value -match '[\s"]') {
    return '"' + ($Value -replace '"', '\"') + '"'
  }
  return $Value
}

function Test-PlaceholderFile {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return $true
  }
  $item = Get-Item -LiteralPath $Path
  if ($item.Length -le 1) {
    return $true
  }
  return $false
}
