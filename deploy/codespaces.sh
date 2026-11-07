#!/usr/bin/env bash
# Démo sans VPS : exécution dans un GitHub Codespace (voir DEMO.md).
#   installer : dépendances (lancé une fois à la création du Codespace)
#   demarrer  : lance le serveur en arrière-plan, rend le port public, écrit les URL dans DEMO.md
set -euo pipefail
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RACINE"

installer() {
  sudo apt-get update -q && sudo apt-get install -y -q ffmpeg >/dev/null
  python3 -m venv .venv
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  bash tools/fetch_reveal.sh
  mkdir -p data
}

demarrer() {
  JETON="${AMPHI_JETON:-demo}"
  if [ -n "${CODESPACE_NAME:-}" ]; then
    URL="https://${CODESPACE_NAME}-8000.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
    # Port public : les téléphones des participants doivent y accéder sans compte GitHub.
    (sleep 5; gh codespace ports visibility 8000:public -c "$CODESPACE_NAME" >/dev/null 2>&1 || true) &
  else
    URL="http://localhost:8000"
  fi
  pkill -f "uvicorn server.app:app" 2>/dev/null || true
  mkdir -p data
  AMPHI_JETON="$JETON" AMPHI_URL_PUBLIQUE="$URL" AMPHI_BDD="$RACINE/data/quiz.sqlite" \
    nohup .venv/bin/uvicorn server.app:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips '*' \
    > data/serveur.log 2>&1 &
  cat > DEMO.md <<EOF
# Démo en cours

Serveur lancé dans ce Codespace. Le port 8000 est rendu public automatiquement (sinon : onglet **Ports**, clic droit sur 8000 → *Port Visibility* → *Public*).

| Vue | URL |
| --- | --- |
| Projecteur (PC 1) | $URL/deck/?role=scene |
| Source OBS (PC 2) | $URL/deck/?role=obs |
| Télécommande (téléphone) | $URL/remote?token=$JETON |
| Éditeur | $URL/editeur?token=$JETON |
| Participants (QR code) | $URL/q |
| Résultats seuls | $URL/q/resultats |
| Classement | $URL/q/classement |
| Santé | $URL/sante |

Jeton : \`$JETON\`. Journal : \`data/serveur.log\`. Relancer : \`bash deploy/codespaces.sh demarrer\`.

Scénario de test rapide : ouvrir le projecteur sur un écran, la télécommande sur le téléphone, scanner le QR code
avec un second téléphone, avancer jusqu'à une diapositive de quiz, **Ouvrir**, répondre, **Fermer**, **Révéler**.
EOF
  echo "serveur démarré : $URL (jeton $JETON)"
}

case "${1:-}" in
  installer) installer ;;
  demarrer) demarrer ;;
  *) echo "usage : $0 installer|demarrer"; exit 2 ;;
esac
