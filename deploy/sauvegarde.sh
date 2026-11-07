#!/usr/bin/env bash
# Après la session : export agrégé CSV, copie de la base SQLite, puis purge des identifiants anonymes.
#   sudo bash /opt/amphi-replay/deploy/sauvegarde.sh
set -euo pipefail
source /etc/amphi-replay.env
HORODATAGE="$(date +%Y-%m-%d_%H%M)"
DOSSIER=/opt/amphi-replay/data/sauvegardes
mkdir -p "$DOSSIER"
curl -fsS "http://127.0.0.1:8000/api/quiz/export?token=$AMPHI_JETON" -o "$DOSSIER/quiz_$HORODATAGE.csv"
sqlite3 "${AMPHI_BDD:-/opt/amphi-replay/data/quiz.sqlite}" ".backup '$DOSSIER/quiz_$HORODATAGE.sqlite'"
curl -fsS -X POST "http://127.0.0.1:8000/api/quiz/purger?token=$AMPHI_JETON" >/dev/null
chown -R amphi:amphi "$DOSSIER"
echo "export : $DOSSIER/quiz_$HORODATAGE.csv ; base copiée ; réponses purgées."
