#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Checks version consistency between pyproject.toml, server.json, and CHANGELOG.md

.DESCRIPTION
    This script extracts version information from pyproject.toml, server.json, and CHANGELOG.md
    and verifies that all versions are consistent across these files.

.EXAMPLE
    .\check-version-consistency.ps1
#>

[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

try {
    Write-Host "Checking version consistency..." -ForegroundColor Cyan
    
    # Extract version from pyproject.toml
    if (-not (Test-Path "pyproject.toml")) {
        throw "pyproject.toml not found in current directory"
    }
    
    $pyprojectMatch = Select-String -Path "pyproject.toml" -Pattern 'version = "([^"]+)"'
    if (-not $pyprojectMatch) {
        throw "Could not find version in pyproject.toml"
    }
    $PYPROJECT_VERSION = $pyprojectMatch.Matches[0].Groups[1].Value
    Write-Host "INFO: pyproject.toml version: $PYPROJECT_VERSION" -ForegroundColor Green
    
    # Extract version from server.json
    if (-not (Test-Path "server.json")) {
        throw "server.json not found in current directory"
    }
    
    $serverJsonContent = Get-Content "server.json" -Raw | ConvertFrom-Json
    $SERVER_VERSION = $serverJsonContent.version
    $PACKAGE_VERSION = $serverJsonContent.packages[0].version
    Write-Host "INFO: server.json version: $SERVER_VERSION" -ForegroundColor Green
    Write-Host "INFO: server.json package version: $PACKAGE_VERSION" -ForegroundColor Green
    
    # Extract version from CHANGELOG.md
    if (-not (Test-Path "CHANGELOG.md")) {
        throw "CHANGELOG.md not found in current directory"
    }
    
    $changelogMatch = Select-String -Path "CHANGELOG.md" -Pattern '## \[([^\]]+)\]' | Select-Object -First 1
    if (-not $changelogMatch) {
        throw "Could not find version in CHANGELOG.md"
    }
    $CHANGELOG_VERSION = $changelogMatch.Matches[0].Groups[1].Value
    Write-Host "INFO: CHANGELOG.md version: $CHANGELOG_VERSION" -ForegroundColor Green
    
    # Check if all versions match
    $errors = @()
    
    if ($PYPROJECT_VERSION -ne $SERVER_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != server.json ($SERVER_VERSION)"
    }
    
    if ($PYPROJECT_VERSION -ne $PACKAGE_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != server.json package ($PACKAGE_VERSION)"
    }
    
    if ($PYPROJECT_VERSION -ne $CHANGELOG_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != CHANGELOG.md ($CHANGELOG_VERSION)"
    }
    
    if ($errors.Count -gt 0) {
        foreach ($error in $errors) {
            Write-Host "ERROR: $error" -ForegroundColor Red
        }
        exit 1
    }
    
    Write-Host "`nAll versions are consistent: $PYPROJECT_VERSION" -ForegroundColor Green
    exit 0
}
catch {
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
