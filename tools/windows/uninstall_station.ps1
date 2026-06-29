param(
  [string]$ProjectRoot = "",
  [string]$DetectorServiceName = "SeatAoiDetector",
  [string]$ControllerServiceName = "SeatAoiController",
  [string]$ShortcutName = "Seat AOI Display",
  [string]$NssmPath = "",
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
      throw "Service did not stop within 30 seconds: $Name. Stop it manually before uninstalling."
    }
    Invoke-Native $Nssm remove $Name confirm
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
Write-Host "Data (trace/images/logs) on D: drive (not deleted) — manually remove if needed: D:\seat-aoi-data\"
Write-Host "Models on D: drive (not deleted) — manually remove if needed: D:\seat-aoi-model\"
