#!/usr/bin/env bash
# Première installation sur un VPS Infomaniak (Debian 12 / Ubuntu 24.04), à lancer en root :
#
#   curl -fsSL https://raw.githubusercontent.com/RomainR-974/amphi-replay/main/deploy/install.sh | bash -s -- q.exemple.fr
#
# ou, après un clone manuel : sudo bash deploy/install.sh q.exemple.fr
#
# Prérequis côté Infomaniak :
#   - VPS (VPS Lite ou Public Cloud), 1 vCPU / 2 Go suffisent ;
#   - ports 80 et 443 ouverts dans le pare-feu du VPS (Manager Infomaniak → VPS → Pare-feu) ;
#   - enregistrement DNS A du sous-domaine vers l'IP du VPS (Manager → Domaines → DNS).
set -euo pipefail

DOMAINE="${1:-}"
DEPOT="${AMPHI_DEPOT:-https://github.com/RomainR-974/amphi-replay.git}"
DEST=/opt/amphi-replay
ENV_FICHIER=/etc/amphi-replay.env

if [ -z "$DOMAINE" ]; then echo "usage : $0 <sous-domaine>  (ex. q.exemple.fr)"; exit 2; fi
if [ "$(id -u)" -ne 0 ]; then echo "à lancer en root (sudo)"; exit 2; fi

echo "== paquets"
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip git curl ffmpeg debian-keyring debian-archive-keyring apt-transport-https sqlite3

if ! command -v caddy >/dev/null; then
  echo "== caddy"
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -q && apt-get install -y -q caddy
fi

echo "== utilisateur et dépôt"
id amphi >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin amphi
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
else
  git clone "$DEPOT" "$DEST"
fi
mkdir -p "$DEST/data"

echo "== environnement python"
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"
bash "$DEST/tools/fetch_reveal.sh"
chown -R amphi:amphi "$DEST"

if [ ! -f "$ENV_FICHIER" ]; then
  echo "== configuration $ENV_FICHIER"
  JETON="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
  sed -e "s|^AMPHI_DOMAINE=.*|AMPHI_DOMAINE=$DOMAINE|" \
      -e "s|^AMPHI_URL_PUBLIQUE=.*|AMPHI_URL_PUBLIQUE=https://$DOMAINE|" \
      -e "s|^AMPHI_JETON=.*|AMPHI_JETON=$JETON|" \
      "$DEST/deploy/env.example" > "$ENV_FICHIER"
  chmod 600 "$ENV_FICHIER"
else
  echo "== $ENV_FICHIER existe déjà, conservé"
fi

echo "== services"
cp "$DEST/deploy/amphi-replay.service" /etc/systemd/system/amphi-replay.service
cp "$DEST/deploy/Caddyfile" /etc/caddy/Caddyfile
mkdir -p /etc/systemd/system/caddy.service.d
printf '[Service]\nEnvironmentFile=%s\n' "$ENV_FICHIER" > /etc/systemd/system/caddy.service.d/amphi.conf
systemctl daemon-reload
systemctl enable --now amphi-replay
systemctl restart caddy

JETON="$(grep '^AMPHI_JETON=' "$ENV_FICHIER" | cut -d= -f2-)"
echo
echo "Installé."
echo "  Participants   : https://$DOMAINE/q"
echo "  Projecteur     : https://$DOMAINE/deck/?role=scene"
echo "  Source OBS     : https://$DOMAINE/deck/?role=obs"
echo "  Télécommande   : https://$DOMAINE/remote?token=$JETON"
echo "  Santé          : https://$DOMAINE/sante"
echo "Mettre à jour   : sudo bash $DEST/deploy/deploy.sh"
