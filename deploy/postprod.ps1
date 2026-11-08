# Post-production sur le PC 2 (Windows) : serveur local + page /deck/postprod.html (voir docs/postprod.md section 3).
#   powershell -ExecutionPolicy Bypass -File deploy\postprod.ps1 [-Captations <dossier>] [-Seance <url>] [-Port 8000] [-Prez <slug>] [-Auto]
#   (AMPHI_SEANCE_JETON : jeton du serveur de seance, prerempli dans la page ; pose par le lanceur de seance)
#   (les formes --captations / --seance sont acceptees aussi)
# Cree le venv si besoin, installe les dependances, verifie ffmpeg, lance uvicorn sur 127.0.0.1:8000 avec
# AMPHI_JETON=local et AMPHI_BDD=data\local.sqlite, puis ouvre le navigateur. Ctrl+C pour arreter.
# Compatible Windows PowerShell 5.

param(
    [string]$Captations = $env:AMPHI_CAPTATIONS,
    [string]$Seance = $env:AMPHI_SEANCE_URL,
    [int]$Port = 8000,
    [string]$Prez = "",
    [switch]$Auto,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Reste
)

# "Continue" et non "Stop" : sous Windows PowerShell 5, avec "Stop", toute ligne écrite sur stderr par un programme
# (python, pip, uvicorn qui y écrit ses journaux) devient une erreur fatale. Les échecs sont testés par $LASTEXITCODE.
$ErrorActionPreference = "Continue"

# Arguments de style GNU (--captations X --seance URL) passes en plus des parametres PowerShell.
for ($i = 0; $i -lt $Reste.Count; $i++) {
    switch ($Reste[$i]) {
        "--captations" { $Captations = $Reste[$i + 1]; $i++ }
        "--seance" { $Seance = $Reste[$i + 1]; $i++ }
        "--port" { $Port = [int]$Reste[$i + 1]; $i++ }
        "--prez" { $Prez = $Reste[$i + 1]; $i++ }
        "--auto" { $Auto = $true }
        default { Write-Warning ("argument ignore : " + $Reste[$i]) }
    }
}

$Racine = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Racine

# Interpreteur Python : py -3 (lanceur Windows) ou python.
$Python = $null
if (Get-Command py -ErrorAction SilentlyContinue) { $Python = "py"; $PythonArgs = @("-3") }
elseif (Get-Command python -ErrorAction SilentlyContinue) { $Python = "python"; $PythonArgs = @() }
if (-not $Python) {
    Write-Host "!! Python introuvable : installer Python 3.11+ depuis https://www.python.org (cocher 'Add python.exe to PATH')." -ForegroundColor Red
    exit 1
}

$VenvPython = Join-Path $Racine ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Host "== creation de l'environnement Python (.venv)"
    & $Python @PythonArgs -m venv .venv
    if ($LASTEXITCODE -ne 0) { Write-Host "!! creation du venv echouee" -ForegroundColor Red; exit 1 }
}
& $VenvPython -c "import fastapi, uvicorn, numpy, multipart" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "== installation des dependances"
    & $VenvPython -m pip install -q --upgrade pip 2>&1 | Out-Host
    & $VenvPython -m pip install -q -r requirements.txt 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Write-Host "!! installation des dependances echouee" -ForegroundColor Red; exit 1 }
}
# Replay interactif : cadrage de l'intervenant (facultatif, docs/replay-interactif.md)
& $VenvPython -c "import cv2" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "== installation d'OpenCV (cadrage du replay interactif)"
    & $VenvPython -m pip install -q -r requirements-postprod.txt 2>&1 | Out-Host
}
if (-not (Test-Path (Join-Path $Racine "deck\vendor\reveal"))) {
    if (Get-Command bash -ErrorAction SilentlyContinue) {
        bash tools/fetch_reveal.sh
    } else {
        Write-Warning "reveal.js non telecharge (bash absent) : les vignettes de la page Presentations seront vides. Lancer 'bash tools/fetch_reveal.sh' depuis Git Bash."
    }
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Host "!! ffmpeg / ffprobe introuvables : installer ffmpeg (winget install Gyan.FFmpeg, ou https://ffmpeg.org) et l'ajouter au PATH." -ForegroundColor Yellow
    Write-Host "   La page se lancera mais l'assemblage sera impossible." -ForegroundColor Yellow
}

New-Item -ItemType Directory -Force -Path (Join-Path $Racine "data") | Out-Null
if ($Captations) {
    New-Item -ItemType Directory -Force -Path $Captations | Out-Null
    $env:AMPHI_CAPTATIONS = $Captations
}
if ($Seance) { $env:AMPHI_SEANCE_URL = $Seance }
if (-not $env:AMPHI_JETON) { $env:AMPHI_JETON = "local" }
$env:AMPHI_BDD = Join-Path $Racine "data\local.sqlite"

$Url = "http://127.0.0.1:$Port/deck/postprod.html?token=$($env:AMPHI_JETON)"
if ($Prez) { $Url += "&prez=$Prez" }
if ($Auto) { $Url += "&auto=1" }  # lanceur de seance, bouton Fini : seances chargees, fichiers preselectionnes
Write-Host "== serveur local : $Url"
$CaptationsAffiche = if ($env:AMPHI_CAPTATIONS) { $env:AMPHI_CAPTATIONS } else { "(defaut : %USERPROFILE%\Videos\Captations)" }
$SeanceAffiche = if ($env:AMPHI_SEANCE_URL) { $env:AMPHI_SEANCE_URL } else { "(journal local)" }
Write-Host "   captations : $CaptationsAffiche ; serveur de seance : $SeanceAffiche"

# Ouvrir le navigateur une fois le serveur pret (travail en arriere-plan, sans bloquer uvicorn).
$Ouvreur = Start-Job -ArgumentList $Url, $Port -ScriptBlock {
    param($Url, $Port)
    for ($i = 0; $i -lt 30; $i++) {
        try {
            Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:$Port/sante" -TimeoutSec 2 | Out-Null
            Start-Process $Url
            return
        } catch { Start-Sleep -Seconds 1 }
    }
}
try {
    & $VenvPython -m uvicorn server.app:app --host 127.0.0.1 --port $Port
} finally {
    Remove-Job -Job $Ouvreur -Force -ErrorAction SilentlyContinue
}
