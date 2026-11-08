#!/usr/bin/env bash
# Mise à jour sur le VPS : git pull + dépendances + redémarrage du service. Pas de conteneur en v1.
#   sudo bash /opt/amphi-replay/deploy/deploy.sh
set -euo pipefail
DEST=/opt/amphi-replay
cd "$DEST"
git config --global --add safe.directory "$DEST" >/dev/null 2>&1 || true
git pull --ff-only
# Import PowerPoint depuis la page Présentations : LibreOffice Impress (rastérisation des objets complexes).
if ! command -v soffice >/dev/null; then
  echo "== installation de LibreOffice Impress (import PowerPoint)"
  DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends libreoffice-impress >/dev/null || echo "!! LibreOffice non installé : import PowerPoint sans rastérisation"
fi
.venv/bin/pip install -q -r requirements.txt
bash tools/fetch_reveal.sh
mkdir -p "$DEST/data" "$DEST/presentations"
for d in "$DEST"/presentations/*/; do [ -d "$d" ] && mkdir -p "${d}medias"; done
chown -R amphi:amphi "$DEST"
cp "$DEST/deploy/amphi-replay.service" /etc/systemd/system/amphi-replay.service
systemctl daemon-reload
systemctl restart amphi-replay
# Attendre que uvicorn écoute (jusqu'à 15 s) avant de vérifier.
for i in $(seq 1 15); do
  if curl -fsS http://127.0.0.1:8000/sante 2>/dev/null; then echo; echo "== service opérationnel"; exit 0; fi
  sleep 1
done
echo "!! le service ne répond pas après 15 s :"
systemctl --no-pager --lines=15 status amphi-replay
exit 1
