<#
.SYNOPSIS
    Build a self-contained mcp-windbg.exe with PyInstaller.

.DESCRIPTION
    Freezes this checkout into one console executable that carries its own Python
    interpreter and every runtime dependency, so it runs on a Windows machine with
    no Python, no uv and no pip. It still needs cdb.exe / kd.exe - the debugger is
    the one thing the binary cannot bundle.

    The build is driven entirely from the flags below, with no committed .spec file
    (the repo's .gitignore excludes *.spec). What the flags do, and why:

      --onefile        One file to download and point a client at. Costs about a
                       second of startup per launch, because the bootstrap unpacks
                       the archive into %TEMP% every time. An MCP server is launched
                       once per session, so that is paid once per session.
      --console        The stdio transport talks JSON-RPC over stdin/stdout. A
                       windowed build has neither.
      --add-data       prompts/*.prompt.md are read at runtime through
                       Path(__file__).parent, which resolves inside the unpacked
                       bundle - so the files have to be laid down at the same
                       relative path the package has.
      --noupx          UPX-compressed binaries are a reliable way to get flagged by
                       antivirus, and a debugging tool starts out suspicious enough.

.PARAMETER OutputPath
    Directory to write mcp-windbg.exe to. Defaults to dist/ in the repo root.

.PARAMETER Verify
    After the build, run the end-to-end scenario suite against the binary that was
    just produced (MCP_WINDBG_SERVER_EXE) instead of against python -m mcp_windbg.
    This is the check that the frozen build actually serves MCP; a build that
    imports cleanly can still be missing a module a tool needs.

.EXAMPLE
    pwsh scripts/Build-Exe.ps1
    Build dist/mcp-windbg.exe.

.EXAMPLE
    pwsh scripts/Build-Exe.ps1 -Verify
    Build it and prove it with the full scenario suite.
#>
[CmdletBinding()]
param(
    [string]$OutputPath,
    [switch]$Verify
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot

if (-not $OutputPath) {
    $OutputPath = Join-Path $repo "dist"
}

$versionMatch = Select-String -Path (Join-Path $repo "pyproject.toml") -Pattern 'version = "([^"]+)"'
if (-not $versionMatch) {
    throw "Could not find version in pyproject.toml"
}
$version = $versionMatch.Matches[0].Groups[1].Value
Write-Host "INFO: building mcp-windbg.exe $version" -ForegroundColor Cyan

$workPath = Join-Path $repo "build"
New-Item -ItemType Directory -Force -Path $workPath | Out-Null

# Windows file metadata. Explorer's Details tab, winget's manifest validation and
# any future code signature all read this; an executable without it reads as
# anonymous. PyInstaller wants its own version-file format, so write one.
$fileVersion = ($version -split '[.+-]')[0..2] -join ','
$versionFile = Join-Path $workPath "version-info.txt"
@"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($fileVersion,0),
    prodvers=($fileVersion,0),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Sven Scharmentke'),
      StringStruct('FileDescription', 'MCP server for Windows crash dump analysis and WinDbg debugging'),
      StringStruct('FileVersion', '$version'),
      StringStruct('InternalName', 'mcp-windbg'),
      StringStruct('LegalCopyright', 'MIT License'),
      StringStruct('OriginalFilename', 'mcp-windbg.exe'),
      StringStruct('ProductName', 'mcp-windbg'),
      StringStruct('ProductVersion', '$version')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@ | Set-Content -Path $versionFile -Encoding utf8NoBOM

$promptsData = "{0};mcp_windbg/prompts" -f (Join-Path $repo "src\mcp_windbg\prompts\*.prompt.md")

$pyinstallerArgs = @(
    "--noconfirm"
    "--onefile"
    "--console"
    "--noupx"
    "--name", "mcp-windbg"
    "--paths", (Join-Path $repo "src")
    "--add-data", $promptsData
    "--version-file", $versionFile
    "--distpath", $OutputPath
    "--workpath", $workPath
    "--specpath", $workPath
    (Join-Path $repo "src\mcp_windbg\__main__.py")
)

Push-Location $repo
try {
    & uv run --group dist pyinstaller @pyinstallerArgs
    if ($LASTEXITCODE -ne 0) {
        throw "pyinstaller failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$exe = Join-Path $OutputPath "mcp-windbg.exe"
if (-not (Test-Path $exe)) {
    throw "pyinstaller reported success but $exe does not exist"
}

$sizeMb = (Get-Item $exe).Length / 1MB
$hash = (Get-FileHash -Path $exe -Algorithm SHA256).Hash
Write-Host ("INFO: {0} ({1:N1} MB)" -f $exe, $sizeMb) -ForegroundColor Green
Write-Host "INFO: SHA256 $hash" -ForegroundColor Green

if ($Verify) {
    Write-Host "INFO: running the scenario suite against the binary" -ForegroundColor Cyan
    Push-Location $repo
    try {
        $env:MCP_WINDBG_SERVER_EXE = $exe
        & uv run pytest src/mcp_windbg/tests/ -q
        $testExit = $LASTEXITCODE
    }
    finally {
        $env:MCP_WINDBG_SERVER_EXE = $null
        Pop-Location
    }
    if ($testExit -ne 0) {
        throw "the scenario suite failed against $exe (exit code $testExit)"
    }
    # The hermetic unit tests in that run still exercise the checkout in-process;
    # it is the scenario suite that drove the binary.
    Write-Host "INFO: the scenarios pass against the binary" -ForegroundColor Green
}
