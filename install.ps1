# Instala o aula-uploader neste computador (Windows / PowerShell).
$ErrorActionPreference = "Stop"
$MinMajor = 3
$MinMinor = 10

function Die($msg) {
  Write-Host ""
  Write-Host "x $msg" -ForegroundColor Red
  Write-Host ""
  exit 1
}

function Ok($msg) { Write-Host "ok $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "-> $msg" }

Write-Host ""
Write-Host "aula-uploader - instalacao"
Write-Host "--------------------------"

# --- Python ---
$py = $null
foreach ($cand in @("py", "python", "python3")) {
  if (Get-Command $cand -ErrorAction SilentlyContinue) {
    $py = $cand
    break
  }
}
if (-not $py) {
  Die "Python nao encontrado. Instale Python $MinMajor.$MinMinor+ em https://www.python.org/downloads/ (marque Add to PATH)."
}

$verArgs = if ($py -eq "py") { @("-3", "-c", "import sys; print('%d.%d' % sys.version_info[:2])") } else { @("-c", "import sys; print('%d.%d' % sys.version_info[:2])") }
$ver = & $py @verArgs
$parts = $ver.Trim().Split(".")
$maj = [int]$parts[0]
$min = [int]$parts[1]
if ($maj -lt $MinMajor -or ($maj -eq $MinMajor -and $min -lt $MinMinor)) {
  Die "Python $ver e antigo demais. Precisa de $MinMajor.$MinMinor+. Instale em https://www.python.org/downloads/ e rode de novo: .\install.ps1"
}
Ok "Python $ver ($py)"

# --- ffmpeg ---
if ((Get-Command ffmpeg -ErrorAction SilentlyContinue) -and (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
  Ok "ffmpeg ok"
} else {
  Info "ffmpeg nao encontrado - tentando instalar com winget..."
  if (Get-Command winget -ErrorAction SilentlyContinue) {
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
  } else {
    Die "Instale o ffmpeg (winget install -e --id Gyan.FFmpeg), feche e reabra o terminal, e rode de novo."
  }
  if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
    Die "ffmpeg instalado, mas ainda nao esta no PATH. Feche e reabra o PowerShell, depois rode .\install.ps1 de novo."
  }
  Ok "ffmpeg ok"
}

# --- pasta do repo ---
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Root

Info "Criando ambiente virtual..."
if ($py -eq "py") {
  & py -3 -m venv .venv
} else {
  & $py -m venv .venv
}

Info "Ativando venv..."
& .\.venv\Scripts\Activate.ps1

Info "Atualizando pip..."
pip install -U pip setuptools wheel | Out-Null

Info "Instalando o app..."
pip install -e .

Ok "Instalacao concluida"
Write-Host ""
Write-Host "Para abrir:"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  aula-uploader web"
Write-Host ""
Write-Host "Depois: http://127.0.0.1:8787/"
Write-Host ""
