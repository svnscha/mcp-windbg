<#
.SYNOPSIS
    Switch Claude Code in this checkout between the released mcp-windbg and the local dev server.

.DESCRIPTION
    Everything is scoped to this checkout (Claude Code's "local" scope), so other projects keep
    using the released plugin either way.

      Released  Update the mcp-windbg marketplace and its three plugins to the latest release,
                remove the local dev server, and re-enable the uvx plugin here.
      Local     Disable the uvx plugin here and register an "mcp-windbg" server that runs this
                working copy (uv run python -m mcp_windbg), so it serves whatever branch is
                checked out.
      Status    Show which of the two is active.

    The skills and agents plugins stay enabled in both modes; they resolve tools by base name,
    so they work with either server. Restart Claude Code after switching.

.PARAMETER Mode
    Released, Local, or Status (the default).

.EXAMPLE
    pwsh scripts/Switch-McpWindbg.ps1 Local
    Try a branch before it is released.

.EXAMPLE
    pwsh scripts/Switch-McpWindbg.ps1 Released
    Go back to the published version, updated to the latest release.
#>
[CmdletBinding()]
param(
    [ValidateSet("Released", "Local", "Status")]
    [string]$Mode = "Status"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$marketplace = "mcp-windbg"
$uvxPlugin = "mcp-windbg-uvx@$marketplace"
$plugins = @($uvxPlugin, "mcp-windbg-skills@$marketplace", "mcp-windbg-agents@$marketplace")
$serverName = "mcp-windbg"
# Same default as the plugin's .mcp.json, so both modes resolve symbols alike.
$symbolPath = if ($env:_NT_SYMBOL_PATH) { $env:_NT_SYMBOL_PATH } else {
    "SRV*C:\Symbols*https://msdl.microsoft.com/download/symbols"
}

function Invoke-Claude {
    param([string[]]$Arguments, [switch]$AllowFailure)
    $output = & claude @Arguments 2>&1
    if ($LASTEXITCODE -ne 0 -and -not $AllowFailure) {
        throw "claude $($Arguments -join ' ') failed:`n$($output -join "`n")"
    }
    $output
}

function Show-Status {
    $installed = Get-Content (Join-Path $HOME ".claude/plugins/installed_plugins.json") -Raw |
        ConvertFrom-Json
    foreach ($plugin in $plugins) {
        $entry = $installed.plugins.$plugin
        $version = if ($entry) { $entry[0].version } else { "not installed" }
        Write-Host ("  {0,-30} {1}" -f $plugin, $version)
    }
    $local = Invoke-Claude @("mcp", "get", $serverName) -AllowFailure
    if ($LASTEXITCODE -eq 0 -and ($local -join "`n") -match "Scope:\s*Local") {
        Write-Host "Active here: local dev server ($repo)"
    } else {
        Write-Host "Active here: released plugin"
    }
}

Push-Location $repo
try {
    switch ($Mode) {
        "Released" {
            Invoke-Claude @("mcp", "remove", $serverName, "-s", "local") -AllowFailure | Out-Null
            Invoke-Claude @("plugin", "marketplace", "update", $marketplace) | Out-Null
            foreach ($plugin in $plugins) {
                Invoke-Claude @("plugin", "update", $plugin) | Select-Object -Last 1 | Write-Host
            }
            Invoke-Claude @("plugin", "enable", $uvxPlugin, "-s", "local") -AllowFailure | Out-Null
        }
        "Local" {
            Invoke-Claude @("plugin", "disable", $uvxPlugin, "-s", "local") -AllowFailure | Out-Null
            Invoke-Claude @("mcp", "remove", $serverName, "-s", "local") -AllowFailure | Out-Null
            Invoke-Claude @("mcp", "add", "-s", "local", $serverName, "-e", "_NT_SYMBOL_PATH=$symbolPath",
                "--", "uv", "run", "--directory", $repo, "python", "-m", "mcp_windbg") | Out-Null
        }
    }
    Show-Status
    if ($Mode -ne "Status") { Write-Host "Restart Claude Code to apply." }
} finally {
    Pop-Location
}
