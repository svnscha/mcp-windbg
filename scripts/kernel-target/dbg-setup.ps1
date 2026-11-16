# Debugger VM setup: Windows SDK debuggers, uv, and the mcp-windbg source in C:\src.
# Expects C:\stage\uv.zip (hash-checked before upload) and C:\stage\src.zip.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$kd = 'C:\Program Files (x86)\Windows Kits\10\Debuggers\x64\kd.exe'
if (-not (Test-Path $kd)) {
  # No winget on Windows Server: the debuggers come from the SDK installer, signature checked first.
  Invoke-WebRequest 'https://go.microsoft.com/fwlink/?linkid=2376217' -OutFile C:\stage\winsdksetup.exe
  $sig = Get-AuthenticodeSignature C:\stage\winsdksetup.exe
  if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') { throw "winsdksetup.exe signature: $($sig.Status) $($sig.SignerCertificate.Subject)" }
  Start-Process C:\stage\winsdksetup.exe -ArgumentList '/features OptionId.WindowsDesktopDebuggers /quiet /norestart' -Wait
}
if (-not (Test-Path C:\uv\uv.exe)) { Expand-Archive C:\stage\uv.zip C:\uv -Force }
if (Test-Path C:\src) { Remove-Item C:\src -Recurse -Force }
Expand-Archive C:\stage\src.zip C:\src
$machinePath = [Environment]::GetEnvironmentVariable('Path', 'Machine')
if ($machinePath -notlike '*C:\uv*') { [Environment]::SetEnvironmentVariable('Path', "$machinePath;C:\uv", 'Machine') }
Set-Location C:\src
# Windows PowerShell turns a native command's stderr into an error record; uv and kd log there.
$ErrorActionPreference = 'Continue'
# 3.13: the newest CPython has no Windows pyyaml wheel yet, and building one needs a C compiler.
C:\uv\uv.exe sync --dev --python 3.13 2>&1 | Select-Object -Last 2
& $kd -? 2>&1 | Select-Object -First 2
