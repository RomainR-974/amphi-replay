#!/usr/bin/env bash
# Mise à jour sur le VPS : git pull + dépendances + redémarrage du service. Pas de conteneur en v1.
#   sudo bash /opt/amphi-replay/deploy/deploy.sh
set -euo pipefail
DEST=/opt/amphi-replay
cd "$DEST"
git config --global --add safe.directory "$DEST" >/dev/null 2>&1 || true

# Les présentations éditées sur le serveur (éditeur, import, questions) sont la référence : on les garde.
# Pour chaque fichier de presentations/ modifié ici, la version de GitHub est mise de côté en <fichier>.github
# (pour comparaison) et la version du serveur est conservée telle quelle.
MODIFIES="$(git diff --name-only -- presentations)"
if [ -n "$MODIFIES" ]; then
  echo "== présentations modifiées sur le serveur, conservées :"
  echo "$MODIFIES" | sed 's/^/   /'
  SAUVE="$(mktemp -d)"
  while IFS= read -r f; do mkdir -p "$SAUVE/$(dirname "$f")"; cp -p "$f" "$SAUVE/$f"; done <<< "$MODIFIES"
  git checkout -q -- presentations
  git pull --ff-only
  while IFS= read -r f; do
    if [ -f "$f" ] && ! cmp -s "$f" "$SAUVE/$f"; then cp -p "$f" "$f.github"; echo "   version GitHub de $f gardée dans $f.github"; fi
    mkdir -p "$(dirname "$f")"; cp -p "$SAUVE/$f" "$f"
  done <<< "$MODIFIES"
  rm -rf "$SAUVE"
else
  git pull --ff-only
fi
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
