#Requires -Version 5.1
<#
.SYNOPSIS
    Vendor wheels and CPython tarballs for fully-offline bootstrap.

.DESCRIPTION
    Run this ONCE on an internet-connected machine after dependencies change.
    It refreshes vendor/wheels and vendor/python, then regenerates
    vendor/MANIFEST.txt (sha256 of every vendored file). The resulting vendor/
    tree is committed to git so clones bootstrap without any network access.

    This script targets PowerShell 7+ but is kept compatible with Windows
    PowerShell 5.1. On Linux/macOS, scripts/vendor.sh remains the preferred path.

.PARAMETER Root
    Repository root directory. Defaults to the parent of this script.

.PARAMETER Verify
    Run the verify logic instead of refresh (delegates to vendor-verify.ps1).
#>
[CmdletBinding()]
param(
    [string]$Root = "",
    [switch]$Verify
)

$ErrorActionPreference = 'Stop'

$ScriptDir = $PSScriptRoot
if (-not $ScriptDir) {
    $ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
if (-not $Root) {
    $Root = Split-Path -Parent $ScriptDir
}
$Root = Resolve-Path $Root | Select-Object -ExpandProperty Path

# Detect Windows in both PowerShell 7+ ($IsWindows) and Windows PowerShell 5.1
# ($env:OS). On Linux/macOS pwsh, $IsWindows is $false and $env:OS is unset.
$IsWin = $false
if (Get-Variable -Name IsWindows -Scope Global -ErrorAction SilentlyContinue) {
    $IsWin = $IsWindows
}
if (-not $IsWin -and $env:OS -eq 'Windows_NT') {
    $IsWin = $true
}

if ($Verify) {
    $verifyScript = Join-Path $ScriptDir 'vendor-verify.ps1'
    & $verifyScript -Root $Root
    exit $LASTEXITCODE
}

$UV_VERSION = '0.12.9'
$PYTHON_TAG = '20260901'

$UvName = if ($IsWin) { 'uv.exe' } else { 'uv' }
$UvxName = if ($IsWin) { 'uvx.exe' } else { 'uvx' }
$UvBin = Join-Path (Join-Path $Root '.tools') $UvName
$UvxBin = Join-Path (Join-Path $Root '.tools') $UvxName

function Assert-LastExit {
    param([int]$Expected = 0)
    if ($LASTEXITCODE -ne $Expected) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

function Ensure-Uv {
    if (Test-Path $UvBin) { return }
    if ($IsWin) {
        $toolsDir = Join-Path $Root '.tools'
        if (-not (Test-Path $toolsDir)) {
            New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
        }
        $tmpZip = Join-Path ([System.IO.Path]::GetTempPath()) 'uv.zip'
        $url = "https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-pc-windows-msvc.zip"
        Invoke-WebRequest -Uri $url -OutFile $tmpZip -UseBasicParsing
        Expand-Archive -Path $tmpZip -DestinationPath $toolsDir -Force
        Remove-Item $tmpZip
        if (-not (Test-Path $UvBin)) {
            throw "uv download failed; check network access to github.com"
        }
        Write-Host "==> uv $UV_VERSION installed from GitHub release"
    }
    else {
        throw "uv not found at $UvBin. Run scripts/bootstrap.sh first."
    }
}

Ensure-Uv

$vendorDir = Join-Path $Root 'vendor'
$wheelsDir = Join-Path $vendorDir 'wheels'
$pythonDir = Join-Path $vendorDir 'python'
$pyTagDir = Join-Path $pythonDir $PYTHON_TAG

if (-not (Test-Path $wheelsDir)) {
    New-Item -ItemType Directory -Path $wheelsDir -Force | Out-Null
}
if (-not (Test-Path $pyTagDir)) {
    New-Item -ItemType Directory -Path $pyTagDir -Force | Out-Null
}

Write-Host '==> exporting locked requirements to vendor/reqs.txt ...'
$reqsFile = Join-Path $vendorDir 'reqs.txt'
& $UvBin export --frozen --no-hashes --extra test --group dev --no-editable -o $reqsFile
Assert-LastExit

Write-Host '==> flattening vendor/reqs.txt to vendor/reqs-flat.txt ...'
$flatFile = Join-Path $vendorDir 'reqs-flat.txt'
$lines = Get-Content $reqsFile -Encoding UTF8
$out = New-Object System.Collections.Generic.List[System.String]
foreach ($line in $lines) {
    $trimmed = $line.Trim()
    if (-not $trimmed) { continue }
    if ($trimmed.StartsWith('#')) { continue }
    if ($trimmed -eq '.') { continue }
    # Match scripts/vendor.sh: strip trailing " # via ..." comments but keep
    # environment markers ("; python_full_version ...").
    $cleaned = $line -replace ' *# .*$', ''
    if ($cleaned) { $out.Add($cleaned) }
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($flatFile, ($out.ToArray() -join "`n") + "`n", $utf8NoBom)

$reqsFlat = Join-Path $vendorDir 'reqs-flat.txt'

# Each OS vendors wheels for itself; the shared wheelhouse ends up with both
# Linux and Windows wheels after running this on each platform. We therefore
# download into the existing wheelhouse instead of wiping it.
Write-Host '==> downloading current-platform wheels ...'
& $UvxBin --from pip pip download -r $reqsFlat -d $wheelsDir
Assert-LastExit

Write-Host '==> downloading hatchling + transitive build deps ...'
& $UvxBin --from pip pip download hatchling editables -d $wheelsDir
Assert-LastExit

$pyFilename = if ($IsWin) {
    "cpython-3.12.14+${PYTHON_TAG}-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"
}
else {
    "cpython-3.12.14+${PYTHON_TAG}-x86_64-unknown-linux-gnu-install_only_stripped.tar.gz"
}
$pyDest = Join-Path $pyTagDir $pyFilename

function Download-PythonTarball {
    param([string]$Dest)
    $filename = Split-Path -Leaf $Dest
    $url = "https://github.com/astral-sh/python-build-standalone/releases/download/${PYTHON_TAG}/$($filename -replace '\+','%2B')"

    if (Test-Path $Dest) {
        Write-Host "==> $filename already present, verifying checksum ..."
        tar -tzf $Dest > $null 2>&1
        if ($LASTEXITCODE -eq 0) {
            Write-Host "    $filename verified"
            return
        }
        Write-Host "    $filename is corrupt, re-downloading ..."
        Remove-Item $Dest
    }

    Write-Host "==> downloading $filename ..."
    Invoke-WebRequest -Uri $url -OutFile $Dest -UseBasicParsing
    tar -tzf $Dest > $null 2>&1
    Assert-LastExit
    Write-Host "    $filename downloaded and verified"
}

Download-PythonTarball -Dest $pyDest

Write-Host '==> generating vendor/MANIFEST.txt ...'
$uvVersion = & $UvBin --version
$pipVersion = & $UvxBin --from pip pip --version
$generated = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
$header = @(
    "# Generated $generated",
    "# uv $uvVersion",
    "# pip $pipVersion"
)

$entries = Get-ChildItem -Path $vendorDir -File -Recurse |
    Where-Object { $_.Name -ne 'MANIFEST.txt' -and $_.Name -ne 'MANIFEST.sha256' } |
    ForEach-Object {
        $rel = $_.FullName.Substring($vendorDir.Length + 1) -replace '\\', '/'
        $hash = (Get-FileHash $_.FullName -Algorithm SHA256).Hash.ToLower()
        "$hash  $rel"
    } | Sort-Object

$manifestPath = Join-Path $vendorDir 'MANIFEST.txt'
$manifestBody = ($header + $entries) -join "`n"
[System.IO.File]::WriteAllText($manifestPath, $manifestBody + "`n", $utf8NoBom)

$manifestHash = (Get-FileHash $manifestPath -Algorithm SHA256).Hash.ToLower()
$hashPath = Join-Path $vendorDir 'MANIFEST.sha256'
[System.IO.File]::WriteAllText($hashPath, $manifestHash + "`n", $utf8NoBom)

$wheelCount = (Get-ChildItem -Path $wheelsDir -Filter '*.whl').Count
$pyCount = (Get-ChildItem -Path $pythonDir -Recurse -Filter '*.tar.gz').Count
$totalBytes = (Get-ChildItem -Path $vendorDir -File -Recurse | Measure-Object -Property Length -Sum).Sum
$totalSize = if ($totalBytes -gt 1GB) { '{0:N1} GB' -f ($totalBytes / 1GB) } else { '{0:N1} MB' -f ($totalBytes / 1MB) }

Write-Host ''
Write-Host 'Vendor summary:'
Write-Host "  wheels      : $wheelCount"
Write-Host "  python tars : $pyCount"
Write-Host "  total size  : $totalSize"
Write-Host ''
Write-Host 'Commit vendor/ to git so clones bootstrap fully offline.'
