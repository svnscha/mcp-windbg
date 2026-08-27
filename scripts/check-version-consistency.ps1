#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Checks that every version in the repository, and the CHANGELOG entry, agree

.DESCRIPTION
    Extracts the version from pyproject.toml, server.json (top level and package),
    .release-please-manifest.json, the Claude plugin manifest, the marketplace entry and the
    server version pinned in the plugin's .mcp.json, and checks they all match. Also checks that
    CHANGELOG.md's top heading names that version and has an entry underneath it.

    Run as a gate before release-please creates a tag: everything it asserts is something that
    cannot be fixed after a release without publishing a second one.

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

    # A heading on its own still matches the version but leaves release-notes with nothing to
    # publish, so read what sits under it, up to the next heading. LineNumber is 1-based and the
    # array is 0-based, so indexing with it starts on the line after the heading.
    $changelogLines = Get-Content "CHANGELOG.md"
    $entryBody = @()
    for ($i = $changelogMatch.LineNumber; $i -lt $changelogLines.Count; $i++) {
        if ($changelogLines[$i].StartsWith("## [")) { break }
        $entryBody += $changelogLines[$i]
    }
    $CHANGELOG_EMPTY = -not ($entryBody | Where-Object { $_.Trim() })

    # The version release-please itself tracks. If this drifts from pyproject.toml the next
    # release is computed from the wrong base.
    $MANIFEST_VERSION = (Get-Content ".release-please-manifest.json" -Raw | ConvertFrom-Json).'.'
    Write-Host "INFO: .release-please-manifest.json version: $MANIFEST_VERSION" -ForegroundColor Green
    
    # Extract versions from the Claude plugin + marketplace manifests. These are
    # kept in step by release-please extra-files entries; checking them here is
    # what catches an entry that silently stopped matching.
    $PLUGIN_VERSION = (Get-Content "plugins/mcp-windbg/.claude-plugin/plugin.json" -Raw | ConvertFrom-Json).version
    $MARKETPLACE_VERSION = (Get-Content ".claude-plugin/marketplace.json" -Raw | ConvertFrom-Json).plugins[0].version
    $mcpArgs = (Get-Content "plugins/mcp-windbg/.mcp.json" -Raw | ConvertFrom-Json).mcpServers.'mcp-windbg'.args
    $PINNED_VERSION = ($mcpArgs[0] -split '@')[-1]
    Write-Host "INFO: plugin.json version: $PLUGIN_VERSION" -ForegroundColor Green
    Write-Host "INFO: marketplace.json plugin version: $MARKETPLACE_VERSION" -ForegroundColor Green
    Write-Host "INFO: plugin .mcp.json pinned server: $PINNED_VERSION" -ForegroundColor Green

    # Check if all versions match
    $errors = @()

    if ($PYPROJECT_VERSION -ne $PLUGIN_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != plugin.json ($PLUGIN_VERSION)"
    }

    if ($PYPROJECT_VERSION -ne $MARKETPLACE_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != marketplace.json ($MARKETPLACE_VERSION)"
    }

    if ($PYPROJECT_VERSION -ne $PINNED_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != plugin .mcp.json pin ($PINNED_VERSION)"
    }
    
    if ($PYPROJECT_VERSION -ne $SERVER_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != server.json ($SERVER_VERSION)"
    }
    
    if ($PYPROJECT_VERSION -ne $PACKAGE_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != server.json package ($PACKAGE_VERSION)"
    }
    
    if ($PYPROJECT_VERSION -ne $CHANGELOG_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != CHANGELOG.md ($CHANGELOG_VERSION)"
    }

    if ($PYPROJECT_VERSION -ne $MANIFEST_VERSION) {
        $errors += "Version mismatch: pyproject.toml ($PYPROJECT_VERSION) != .release-please-manifest.json ($MANIFEST_VERSION)"
    }

    if ($CHANGELOG_EMPTY) {
        $errors += "CHANGELOG.md has a '## [$CHANGELOG_VERSION]' heading with nothing under it - the release notes would be empty"
    }
    
    if ($errors.Count -gt 0) {
        # Not $error: that is a read-only PowerShell automatic variable, and
        # assigning it threw before any mismatch could be reported.
        foreach ($mismatch in $errors) {
            Write-Host "ERROR: $mismatch" -ForegroundColor Red
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
