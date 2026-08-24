# Windows: right-click and choose "Run with PowerShell" (or: powershell -ExecutionPolicy Bypass -File start.ps1)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host "Installing uv (Python package manager, one-time)..."
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}
Write-Host "Installing Python 3.12 and reelnotes dependencies (the first run takes about a minute)..."
uv python install 3.12 --quiet
uv sync --python 3.12 --extra all --extra local --upgrade-package yt-dlp --quiet
if ($args.Count -gt 0) { uv run reelnotes @args } else { uv run reelnotes web }
