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
    throw "命令执行失败，exit=${LASTEXITCODE}: $($Command -join ' ')"
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

  throw "未找到 nssm.exe。请把 nssm.exe 放到 bin\nssm.exe、tools\nssm\nssm.exe，或通过 -NssmPath 指定。"
}

function Remove-ServiceIfExists {
  param([string]$Nssm, [string]$Name)
  $service = Get-Service -Name $Name -ErrorAction SilentlyContinue
  if ($null -ne $service) {
    Invoke-NativeOptional $Nssm stop $Name
    Invoke-Native $Nssm remove $Name confirm
    Write-Host "已移除服务: $Name"
  } else {
    Write-Host "服务不存在，跳过: $Name"
  }
}

function Remove-ShortcutIfExists {
  param([string]$Path)
  if (Test-Path -LiteralPath $Path) {
    Remove-Item -LiteralPath $Path -Force
    Write-Host "已删除快捷方式: $Path"
  }
}

if (-not (Test-IsAdministrator)) {
  throw "卸载后台服务需要管理员权限。请用管理员身份运行 PowerShell。"
}

$ProjectRoot = Resolve-ProjectRoot -Value $ProjectRoot
$Nssm = Resolve-Nssm -ExplicitPath $NssmPath -Root $ProjectRoot

Remove-ServiceIfExists -Nssm $Nssm -Name $ControllerServiceName
Remove-ServiceIfExists -Nssm $Nssm -Name $DetectorServiceName

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

Write-Host "Seat Surface AOI 服务和快捷方式已卸载。项目目录、模型、trace 和日志未删除: $ProjectRoot"
