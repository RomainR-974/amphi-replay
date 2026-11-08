#!/usr/bin/env bash
# Post-production sur le PC 2 (Linux / macOS) : serveur local + page /deck/postprod.html (voir docs/postprod.md §3).
#   bash deploy/postprod.sh [--captations <dossier>] [--seance <url>] [--port <n>]
# Crée le venv si besoin, installe les dépendances, vérifie ffmpeg, lance uvicorn sur 127.0.0.1:8000 avec
# AMPHI_JETON=local et AMPHI_BDD=data/local.sqlite, puis ouvre le navigateur. Ctrl+C pour arrêter.
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RACINE"

CAPTATIONS="${AMPHI_CAPTATIONS:-}"
SEANCE="${AMPHI_SEANCE_URL:-}"
PORT=8000
while [ $# -gt 0 ]; do
  case "$1" in
    --captations) CAPTATIONS="$2"; shift 2 ;;
    --seance) SEANCE="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    -h|--help) sed -n '2,5p' "$0"; exit 0 ;;
    *) echo "argument inconnu : $1" >&2; exit 2 ;;
  esac
done

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null; then echo "!! python3 introuvable : installer Python 3.11+" >&2; exit 1; fi
if [ ! -x .venv/bin/python ]; then
  echo "== création de l'environnement Python (.venv)"
  "$PYTHON" -m venv .venv
fi
if ! .venv/bin/python -c "import fastapi, uvicorn, numpy" 2>/dev/null; then
  echo "== installation des dépendances"
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
fi
[ -d deck/vendor/reveal ] || bash tools/fetch_reveal.sh || echo "!! reveal.js non téléchargé : les vignettes de la page Présentations seront vides"

if ! command -v ffmpeg >/dev/null || ! command -v ffprobe >/dev/null; then
  echo "!! ffmpeg / ffprobe introuvables : installer ffmpeg (Debian/Ubuntu : sudo apt install ffmpeg ; macOS : brew install ffmpeg)." >&2
  echo "   La page se lancera mais l'assemblage sera impossible." >&2
fi

mkdir -p data
if [ -n "$CAPTATIONS" ]; then
  mkdir -p "$CAPTATIONS"
  export AMPHI_CAPTATIONS="$CAPTATIONS"
fi
[ -n "$SEANCE" ] && export AMPHI_SEANCE_URL="$SEANCE"
export AMPHI_JETON="${AMPHI_JETON:-local}"
export AMPHI_BDD="$RACINE/data/local.sqlite"

URL="http://127.0.0.1:$PORT/deck/postprod.html?token=$AMPHI_JETON"
echo "== serveur local : $URL"
echo "   captations : ${AMPHI_CAPTATIONS:-(défaut : ~/Vidéos/Captations)} ; serveur de séance : ${AMPHI_SEANCE_URL:-(journal local)}"
# Ouvrir le navigateur une fois le serveur prêt (sans bloquer).
(
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:$PORT/sante" >/dev/null 2>&1; then
      if command -v xdg-open >/dev/null; then xdg-open "$URL" >/dev/null 2>&1 || true
      elif command -v open >/dev/null; then open "$URL" || true
      else echo "   ouvrir dans le navigateur : $URL"; fi
      exit 0
    fi
    sleep 1
  done
) &
exec .venv/bin/uvicorn server.app:app --host 127.0.0.1 --port "$PORT"
