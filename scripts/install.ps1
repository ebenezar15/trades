# IndexEdge installer for Windows 10/11.
# Run by double-clicking install.bat in the project folder (it calls this script with the right policy).
# Safe to re-run: it updates the environment in place.

param(
    [switch]$NoShortcut,
    [switch]$NoAutoStart,
    [switch]$SkipSetup
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

Step "IndexEdge installer - folder: $Root"

# 1. uv (fast Python package manager, installs its own Python - no admin needed)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Step "Installing uv"
    powershell -ExecutionPolicy Bypass -NoProfile -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv installation failed. Install from https://docs.astral.sh/uv/ and re-run." }
}

# 2. Python + virtual environment + dependencies
Step "Installing Python 3.11 and dependencies (first run takes a few minutes)"
uv python install 3.11
if (-not (Test-Path ".venv")) { uv venv --python 3.11 .venv }
uv pip install --python .venv\Scripts\python.exe -e .
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

# 3. Frontend (prebuilt bundle ships in frontend/dist; rebuild only if Node is present and dist is missing)
if (-not (Test-Path "frontend\dist\index.html")) {
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        Step "Building the web app"
        Push-Location frontend; npm ci; npm run build; Pop-Location
    } else {
        throw "frontend\dist is missing and Node.js is not installed. Download the release ZIP which includes it."
    }
}

# 4. Data folder
New-Item -ItemType Directory -Force -Path "data" | Out-Null

# 5. First-time setup wizard (broker keys go to Windows Credential Manager)
if (-not $SkipSetup) {
    Step "Setup wizard (press Enter to skip any question; you can do this later in Settings)"
    .venv\Scripts\python.exe -m indexedge setup
}

# 6. Desktop shortcut
if (-not $NoShortcut) {
    Step "Creating desktop shortcut"
    $shell = New-Object -ComObject WScript.Shell
    $lnk = $shell.CreateShortcut("$([Environment]::GetFolderPath('Desktop'))\IndexEdge.lnk")
    $lnk.TargetPath = "$Root\IndexEdge.bat"
    $lnk.WorkingDirectory = $Root
    $lnk.IconLocation = "$Root\frontend\dist\favicon.ico,0"
    $lnk.Description = "IndexEdge trading assistant"
    $lnk.Save()
}

# 7. Optional auto-start on weekdays at 08:25 (before the 08:30 login job)
if (-not $NoAutoStart) {
    $answer = Read-Host "Start IndexEdge automatically at 08:25 on weekdays? (y/N)"
    if ($answer -match '^[Yy]') {
        $action = New-ScheduledTaskAction -Execute "$Root\IndexEdge.bat" -Argument "--no-browser" -WorkingDirectory $Root
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 8:25am
        $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries
        Register-ScheduledTask -TaskName "IndexEdge" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
        Write-Host "Scheduled task 'IndexEdge' created. (Your PC must be on and logged in.)"
    }
}

# 8. Firewall rule so your phone on home Wi-Fi can open the app
$fw = Read-Host "Allow phones on your home Wi-Fi to open IndexEdge (adds a private-network firewall rule, needs admin)? (y/N)"
if ($fw -match '^[Yy]') {
    Start-Process powershell -Verb RunAs -ArgumentList "-NoProfile -Command New-NetFirewallRule -DisplayName IndexEdge -Direction Inbound -Protocol TCP -LocalPort 8000 -Profile Private -Action Allow"
}

Step "Done. Double-click 'IndexEdge' on your desktop to start. The app opens at http://localhost:8000"
