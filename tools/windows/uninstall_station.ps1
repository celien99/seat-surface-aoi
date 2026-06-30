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

Import-Module -Force "$PSScriptRoot\module\SeatAoiDeployment\SeatAoiDeployment.psd1"

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

Remove-ServiceIfExists -Nssm $Nssm -Name $DetectorServiceName -Context "uninstall"
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
