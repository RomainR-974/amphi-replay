#!/usr/bin/env bash
# Mise à jour sur le VPS : git pull + dépendances + redémarrage du service. Pas de conteneur en v1.
#   sudo bash /opt/amphi-replay/deploy/deploy.sh
set -euo pipefail
DEST=/opt/amphi-replay
cd "$DEST"
git pull --ff-only
.venv/bin/pip install -q -r requirements.txt
bash tools/fetch_reveal.sh
mkdir -p "$DEST/data" "$DEST/deck/medias"
chown -R amphi:amphi "$DEST"
cp "$DEST/deploy/amphi-replay.service" /etc/systemd/system/amphi-replay.service
systemctl daemon-reload
systemctl restart amphi-replay
sleep 1
systemctl --no-pager --lines=5 status amphi-replay
curl -fsS http://127.0.0.1:8000/sante && echo
