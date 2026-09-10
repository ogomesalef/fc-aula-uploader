#!/usr/bin/env bash
# Instala o aula-uploader neste computador (macOS / Linux).
set -euo pipefail

min_major=3
min_minor=10

die() {
  echo ""
  echo "✗ $*"
  echo ""
  exit 1
}

ok() { echo "✓ $*"; }
info() { echo "→ $*"; }

echo ""
echo "aula-uploader — instalação"
echo "--------------------------"

# --- Python ---
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
[[ -n "$PY" ]] || die "Python não encontrado. Instale Python ${min_major}.${min_minor}+ (ex.: brew install python@3.12)."

ver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
maj="${ver%%.*}"
min="${ver#*.}"
if (( maj < min_major || (maj == min_major && min < min_minor) )); then
  die "Python ${ver} é antigo demais. Precisa de ${min_major}.${min_minor}+.
  No Mac: brew install python@3.12
  Depois rode de novo: bash install.sh"
fi
ok "Python ${ver} ($PY)"

# --- ffmpeg ---
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ok "ffmpeg ok"
else
  info "ffmpeg não encontrado — tentando instalar…"
  if command -v brew >/dev/null 2>&1; then
    brew install ffmpeg
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y ffmpeg
  else
    die "Instale o ffmpeg e rode de novo (Mac: brew install ffmpeg)."
  fi
  ok "ffmpeg instalado"
fi

# --- pasta do repo ---
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

info "Criando ambiente virtual…"
"$PY" -m venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

info "Atualizando pip…"
pip install -U pip setuptools wheel >/dev/null

info "Instalando o app…"
pip install -e .

ok "Instalação concluída"
echo ""
echo "Para abrir:"
echo "  source .venv/bin/activate"
echo "  aula-uploader web"
echo ""
echo "Depois: http://127.0.0.1:8787/"
echo ""
