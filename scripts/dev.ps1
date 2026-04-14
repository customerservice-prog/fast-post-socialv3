# FastPost Social v3 — Windows PowerShell helpers (repo root = parent of scripts/).
# Usage (from repo root):
#   .\scripts\dev.ps1 install
#   .\scripts\dev.ps1 playwright
#   .\scripts\dev.ps1 dev

param(
    [Parameter(Position = 0)]
    [ValidateSet('install', 'playwright', 'playwright-deps', 'dev', 'start-prod', 'help')]
    [string]$Command = 'help'
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Py {
    param([string[]]$Args)
    & python @Args
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

switch ($Command) {
    'help' {
        Write-Host "FastPost dev (PowerShell)"
        Write-Host "  .\scripts\dev.ps1 install          pip install -r requirements.txt"
        Write-Host "  .\scripts\dev.ps1 playwright     Chromium download"
        Write-Host "  .\scripts\dev.ps1 playwright-deps  Chromium + deps (WSL/Linux style; may need admin)"
        Write-Host "  .\scripts\dev.ps1 dev            Flask dev server http://localhost:5000"
        Write-Host "  .\scripts\dev.ps1 start-prod      sh start.sh (Git Bash / WSL)"
    }
    'install' {
        Invoke-Py -Args @('-m', 'pip', 'install', '-r', 'requirements.txt')
    }
    'playwright' {
        Invoke-Py -Args @('-m', 'pip', 'install', '-r', 'requirements.txt')
        Set-Location (Join-Path $Root 'backend')
        Invoke-Py -Args @('-m', 'playwright', 'install', 'chromium')
    }
    'playwright-deps' {
        Invoke-Py -Args @('-m', 'pip', 'install', '-r', 'requirements.txt')
        Invoke-Py -Args @('-m', 'playwright', 'install', '--with-deps', 'chromium')
    }
    'dev' {
        Invoke-Py -Args @('-m', 'pip', 'install', '-r', 'requirements.txt')
        Set-Location (Join-Path $Root 'backend')
        Invoke-Py -Args @('app.py')
    }
    'start-prod' {
        Invoke-Py -Args @('-m', 'pip', 'install', '-r', 'requirements.txt')
        $start = Join-Path $Root 'start.sh'
        if (Get-Command sh -ErrorAction SilentlyContinue) {
            & sh $start
        }
        else {
            throw "start-prod needs 'sh' (Git Bash or WSL). On Windows use: .\scripts\dev.ps1 dev"
        }
    }
}
