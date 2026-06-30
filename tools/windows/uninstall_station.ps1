param(
  [string]$ProjectRoot = "",
  [string]$DetectorServiceName = "SeatAoiDetector",
  [string]$ControllerServiceName = "SeatAoiController",
  [string]$ShortcutName = "Seat AOI Display",
  [string]$NssmPath = "",
  [string]$DataRoot = "",
  [string]$ModelRoot = "",
  [switch]$RemoveStartupShortcut,
  [switch]$CurrentUserShortcut
)

$ErrorActionPreference = "Stop"

function Resolve-ProjectRoot {
  param([string]$Value)
  if ($Value) {
    return (Resolve-Path -LiteralPath $Value).Path
  }
  return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
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

function Test-IsAdministrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = [Security.Principal.WindowsPrincipal]::new($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

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
  param([string]$Nssm, [string]$Name)
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($null -ne $service) {
    Invoke-NativeOptional -ArgList @($Nssm, "stop", $Name)
    if (-not (Wait-ServiceStopped -Name $Name)) {
      throw "Service did not stop within 30 seconds: $Name. Stop it manually before uninstalling."
    }
    Invoke-Native -ArgList @($Nssm, "remove", $Name, "confirm")
    Write-Host "Removed service: $Name"
  } else {
    Write-Host "Service not found, skipped: $Name"
  }
}

function Remove-ShortcutIfExists {
  param([string]$Path)
  if (Test-Path -LiteralPath $Path) {
    Remove-Item -LiteralPath $Path -Force
    Write-Host "Removed shortcut: $Path"
  }
}

if (-not (Test-IsAdministrator)) {
  throw "Administrator PowerShell is required to uninstall Windows services."
}

$ProjectRoot = Resolve-ProjectRoot -Value $ProjectRoot
$DataRoot = Resolve-DeploymentRoot -Value $DataRoot -DefaultLeafName "seat-aoi-data" -Root $ProjectRoot
$ModelRoot = Resolve-DeploymentRoot -Value $ModelRoot -DefaultLeafName "seat-aoi-model" -Root $ProjectRoot
$Nssm = Resolve-Nssm -ExplicitPath $NssmPath -Root $ProjectRoot

Remove-ServiceIfExists -Nssm $Nssm -Name $DetectorServiceName
Remove-ServiceIfExists -Nssm $Nssm -Name $ControllerServiceName

if ($CurrentUserShortcut) {
  $desktop = [Environment]::GetFolderPath("Desktop")
} else {
  $desktop = Join-Path $env:PUBLIC "Desktop"
}
Remove-ShortcutIfExists -Path (Join-Path $desktop "$ShortcutName.lnk")

if ($RemoveStartupShortcut) {
  $startup = [Environment]::GetFolderPath("Startup")
  Remove-ShortcutIfExists -Path (Join-Path $startup "$ShortcutName.lnk")
}

Write-Host "Seat Surface AOI services and shortcuts were uninstalled."
Write-Host "Project code, config: $ProjectRoot (not deleted)"
Write-Host "Data (trace/images/logs) not deleted -- manually remove if needed: $DataRoot"
Write-Host "Models not deleted -- manually remove if needed: $ModelRoot"
