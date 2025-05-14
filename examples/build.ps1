# Launch this script from Developer Command Prompt for Visual Studio

$examplesDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

$buildDir = "build"
$outDir = Join-Path $buildDir "out"

# Ensure output directories exist
New-Item -ItemType Directory -Path $buildDir -Force | Out-Null
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

# Build all .cpp files in examples directory
Get-ChildItem -Path $examplesDir -Filter *.cpp | ForEach-Object {
    $base = $_.BaseName
    $src = $_.FullName
    $obj = Join-Path $buildDir ($base + ".obj")
    $exe = Join-Path $outDir ($base + ".exe")
    Write-Host "Building $src -> $exe ..."
    cl.exe /nologo /Zi /O2 /MT /DNDEBUG /EHsc /Fo:$obj /Fe:$exe $src /link /INCREMENTAL:NO
}
Write-Host "Done."
