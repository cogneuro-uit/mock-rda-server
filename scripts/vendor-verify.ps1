#Requires -Version 5.1
<#
.SYNOPSIS
    Verify the integrity of vendor/ against vendor/MANIFEST.txt.

.DESCRIPTION
    Checks vendor/MANIFEST.sha256 against the hash of vendor/MANIFEST.txt,
    then verifies every file listed in the manifest by sha256. Exits 1 on any
    mismatch or missing file.

.PARAMETER Root
    Repository root directory. Defaults to the parent of this script.
#>
[CmdletBinding()]
param(
    [string]$Root = ""
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

$vendorDir = Join-Path $Root 'vendor'
$manifestPath = Join-Path $vendorDir 'MANIFEST.txt'
$hashPath = Join-Path $vendorDir 'MANIFEST.sha256'

if (-not (Test-Path $manifestPath)) {
    Write-Error 'ERROR: vendor/MANIFEST.txt not found.' -ErrorAction Stop
}
if (-not (Test-Path $hashPath)) {
    Write-Error 'ERROR: vendor/MANIFEST.sha256 not found.' -ErrorAction Stop
}

Write-Host '==> verifying vendor/MANIFEST.txt ...'
$expectedManifest = (Get-Content $hashPath -Raw).Trim()
$actualManifest = (Get-FileHash $manifestPath -Algorithm SHA256).Hash.ToLower()
if ($expectedManifest -ne $actualManifest) {
    Write-Error "ERROR: MANIFEST.txt hash mismatch (expected $expectedManifest, got $actualManifest)" -ErrorAction Stop
}

Write-Host '==> verifying vendored files against vendor/MANIFEST.txt ...'
$mismatches = 0
$missing = 0

Push-Location $vendorDir
try {
    $lines = Get-Content $manifestPath -Encoding UTF8
    foreach ($line in $lines) {
        if (-not $line -or $line.StartsWith('#')) { continue }
        # Split on exactly two spaces, matching scripts/vendor-verify.sh semantics.
        $parts = $line.Split(@('  '), 2, 'None')
        if ($parts.Count -lt 2) { continue }
        $expectedHash = $parts[0]
        $rel = $parts[1]

        if (-not (Test-Path $rel)) {
            Write-Host "  MISSING: $rel" -ForegroundColor Red
            $missing++
            continue
        }

        $actualHash = (Get-FileHash $rel -Algorithm SHA256).Hash.ToLower()
        if ($actualHash -ne $expectedHash) {
            Write-Host "  MISMATCH: $rel (expected $expectedHash, got $actualHash)" -ForegroundColor Red
            $mismatches++
        }
    }
}
finally {
    Pop-Location
}

if ($missing -gt 0 -or $mismatches -gt 0) {
    Write-Error "ERROR: $missing missing, $mismatches mismatched." -ErrorAction Stop
}

Write-Host '==> all vendored files passed the integrity check'
