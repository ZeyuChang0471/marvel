<#
.SYNOPSIS
    Build the MARVEL desktop launcher (MARVEL.exe) and put it on the Desktop.

.DESCRIPTION
    Compiles scripts/desktop_launcher/MarvelLauncher.cs with the .NET Framework
    compiler that ships with Windows, so this needs no network and no PyInstaller
    (which cannot be installed when the network is down — the reason this route was
    chosen on 2026-10-08).

    The old MARVEL.bat is renamed to MARVEL.bat.bak rather than deleted: the exe
    supersedes it, but nothing the user wrote is thrown away.

.EXAMPLE
    pwsh -File scripts/build_desktop_launcher.ps1
    pwsh -File scripts/build_desktop_launcher.ps1 -Target "$env:USERPROFILE\Desktop\MARVEL.exe"
#>
[CmdletBinding()]
param(
    [string]$Target = (Join-Path $env:USERPROFILE 'Desktop\MARVEL.exe'),
    [switch]$KeepBat
)

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot 'desktop_launcher\MarvelLauncher.cs'
$icon = Join-Path $repoRoot 'assets\marvel.ico'

Write-Host "仓库: $repoRoot"

# ── 1. compiler ────────────────────────────────────────────────────────────────
$cscCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$csc = $cscCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $csc) {
    throw "找不到 csc.exe（.NET Framework 编译器）。请安装 .NET Framework 4.x，或改用 PyInstaller 打包。"
}
Write-Host "编译器: $csc"

# ── 2. icon ────────────────────────────────────────────────────────────────────
if (-not (Test-Path $icon)) {
    $python = Join-Path $repoRoot 'venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { $python = 'python' }
    Write-Host "生成图标: $icon"
    & $python (Join-Path $PSScriptRoot 'desktop_launcher\make_icon.py') $icon
    if ($LASTEXITCODE -ne 0) { throw "图标生成失败" }
}

# ── 3. the "already running" path must be exercised, not assumed ───────────────
if (Test-Path $Target) {
    $backup = "$Target.bak"
    Copy-Item $Target $backup -Force
    Write-Host "已备份旧 exe 到 $backup"
}

# ── 4. compile ─────────────────────────────────────────────────────────────────
# /codepage:65001 because the source contains Chinese strings; without it the
# compiler reads them in the system ANSI codepage and the UI text turns to mojibake.
$arguments = @(
    '/nologo',
    '/target:exe',
    '/platform:anycpu',
    '/optimize+',
    '/codepage:65001',
    "/win32icon:$icon",
    "/out:$Target",
    $source
)
Write-Host "编译 -> $Target"
& $csc $arguments
if ($LASTEXITCODE -ne 0) { throw "编译失败（退出码 $LASTEXITCODE）" }

# ── 5. retire the .bat ─────────────────────────────────────────────────────────
$bat = Join-Path (Split-Path -Parent $Target) 'MARVEL.bat'
if ((Test-Path $bat) -and -not $KeepBat) {
    $retired = "$bat.bak"
    Move-Item $bat $retired -Force
    Write-Host "旧的 MARVEL.bat 已改名为 $(Split-Path -Leaf $retired)（没有删除）"
}

$info = Get-Item $Target
Write-Host ""
Write-Host ("完成: {0}  ({1:N0} bytes, {2})" -f $info.FullName, $info.Length, $info.LastWriteTime)
