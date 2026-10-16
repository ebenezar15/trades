# Update IndexEdge in place: pull latest code (if this is a git checkout) and refresh dependencies.
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (Test-Path ".git") {
    git pull --ff-only
} else {
    Write-Host "Not a git checkout: download the latest release ZIP and extract it over this folder (your data\ folder is kept)."
}
uv pip install --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -m indexedge migrate
Write-Host "Updated. Restart IndexEdge."
