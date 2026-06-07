<#
.SYNOPSIS
    Normalize Markdown typography for the documentation.

.DESCRIPTION
    Enforces the Markdown typography rules in .claude/rules/markdown.md across every
    *.md file under -Path:

      - Replaces em dashes (U+2014) and en dashes (U+2013) with a plain hyphen "-".
      - Reports any emoji / pictographic characters, which are not allowed. These
        are NOT auto-removed (the right replacement is wording, not deletion) - fix
        them by hand.

    Files are written back as UTF-8 without BOM; line endings are left untouched.

.PARAMETER Path
    Root folder to scan. Defaults to the repo root (the rule applies to every
    Markdown file); vendored/generated trees are skipped.

.PARAMETER Check
    Report only; do not modify files. Exits 1 if any file would change or any
    emoji is found. Use this in CI or a pre-commit check.

.EXAMPLE
    pwsh scripts/Format-Docs.ps1
    Fix dashes across the repo and list any emoji.

.EXAMPLE
    pwsh scripts/Format-Docs.ps1 -Path docs -Check
    Verify just the docs/ tree without writing (non-zero exit if anything is off).
#>
[CmdletBinding()]
param(
    [string]$Path = (Join-Path $PSScriptRoot '..'),
    [switch]$Check
)

$ErrorActionPreference = 'Stop'
$utf8NoBom = [System.Text.UTF8Encoding]::new($false)

# Surrogate pairs (astral emoji like the construction sign / party popper) plus the
# common BMP pictographic blocks (misc symbols, dingbats incl. check/cross marks)
# and the variation selector.
$emoji = [regex]'[\p{Cs}☀-➿⬀-⯿️]'
$fancyDash = [regex]'[—–]'

$skip = '[\\/](node_modules|site|build|dist|\.venv|\.git)[\\/]'

$files = Get-ChildItem -Path $Path -Recurse -Filter *.md -File |
    Where-Object { $_.FullName -notmatch $skip }

$changed = [System.Collections.Generic.List[string]]::new()
$emojiHits = [System.Collections.Generic.List[string]]::new()

foreach ($f in $files) {
    $text = [System.IO.File]::ReadAllText($f.FullName)
    $fixed = $fancyDash.Replace($text, '-')

    if ($fixed -ne $text) {
        $changed.Add($f.FullName)
        if (-not $Check) {
            [System.IO.File]::WriteAllText($f.FullName, $fixed, $utf8NoBom)
        }
    }

    $lines = $fixed -split "`n"
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($emoji.IsMatch($lines[$i])) {
            $emojiHits.Add(('{0}:{1}' -f $f.FullName, ($i + 1)))
        }
    }
}

Write-Host ("Scanned {0} markdown file(s) under {1}" -f $files.Count, (Resolve-Path $Path))

if ($changed.Count) {
    $verb = if ($Check) { 'would change' } else { 'fixed dashes in' }
    Write-Host ("{0} {1} file(s):" -f $verb, $changed.Count)
    $changed | ForEach-Object { Write-Host "  $_" }
} else {
    Write-Host 'No em/en dashes found.'
}

if ($emojiHits.Count) {
    Write-Warning ("Emoji found (remove by hand - see .claude/rules/markdown.md): {0}" -f $emojiHits.Count)
    $emojiHits | ForEach-Object { Write-Warning "  $_" }
}

if ($Check -and ($changed.Count -or $emojiHits.Count)) {
    exit 1
}
