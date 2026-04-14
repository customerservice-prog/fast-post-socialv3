# Export Facebook session for FastPost (Playwright storage_state).
# Run from ANY folder, e.g.:
#   & "C:\path\to\fast-post-socialv3\scripts\export_facebook_storage.ps1"
# Or from repo root:
#   .\scripts\export_facebook_storage.ps1
#
# Requires: Python with playwright installed (same venv you use for the backend).

$ErrorActionPreference = "Stop"
$ScriptDir = $PSScriptRoot
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location $RepoRoot

$py = Join-Path $ScriptDir "export_facebook_storage.py"
Write-Host "Working directory: $RepoRoot" -ForegroundColor Cyan
& python $py @args
