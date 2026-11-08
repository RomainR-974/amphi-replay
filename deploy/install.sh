#!/usr/bin/env bash
# Première installation sur un VPS Infomaniak (Debian 12 / Ubuntu 24.04), à lancer en root :
#
#   curl -fsSL https://raw.githubusercontent.com/RomainR-974/amphi-replay/main/deploy/install.sh | bash -s -- q.exemple.fr
#
# ou, après un clone manuel : sudo bash deploy/install.sh q.exemple.fr
#
# Prérequis (Infomaniak, Hostinger ou tout VPS Debian/Ubuntu) :
#   - 1 vCPU / 2 Go suffisent ;
#   - ports 80 et 443 ouverts dans le pare-feu de l'hébergeur s'il y en a un ;
#   - enregistrement DNS A du sous-domaine vers l'IP du VPS, propagé AVANT de lancer le script.
# Si Nginx tourne déjà (autres sites), le script s'y branche (site dédié + certbot) au lieu d'installer Caddy.
set -euo pipefail

DOMAINE="${1:-}"
DEPOT="${AMPHI_DEPOT:-https://github.com/RomainR-974/amphi-replay.git}"
DEST=/opt/amphi-replay
ENV_FICHIER=/etc/amphi-replay.env

if [ -z "$DOMAINE" ]; then echo "usage : $0 <sous-domaine>  (ex. q.exemple.fr)"; exit 2; fi
if [ "$(id -u)" -ne 0 ]; then echo "à lancer en root (sudo)"; exit 2; fi

echo "== paquets"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q python3 python3-venv python3-pip git curl ffmpeg debian-keyring debian-archive-keyring apt-transport-https sqlite3
# LibreOffice Impress (soffice) : rastérisation des objets complexes à l'import d'un PowerPoint (sans lui, import dégradé).
apt-get install -y -q --no-install-recommends libreoffice-impress

# Reverse proxy HTTPS : Caddy par défaut ; si Nginx tourne déjà (autres sites sur le serveur), on s'y branche
# avec un site dédié et un certificat Let's Encrypt obtenu par certbot, sans toucher aux autres sites.
if systemctl is-active --quiet nginx; then
  PROXY=nginx
  echo "== nginx déjà actif : site dédié + certbot (Caddy non installé)"
  apt-get install -y -q certbot python3-certbot-nginx
else
  PROXY=caddy
  if ! command -v caddy >/dev/null; then
    echo "== caddy"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -q && apt-get install -y -q caddy
  fi
fi

# Pare-feu Ubuntu éventuel : ouvrir HTTP/HTTPS si ufw est actif.
if command -v ufw >/dev/null && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null && ufw allow 443/tcp >/dev/null && echo "== ufw : ports 80 et 443 ouverts"
fi

echo "== utilisateur et dépôt"
id amphi >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin amphi
git config --global --add safe.directory "$DEST" >/dev/null 2>&1 || true
if [ -d "$DEST/.git" ]; then
  git -C "$DEST" pull --ff-only
else
  git clone "$DEPOT" "$DEST"
fi
# Chaque présentation a son dossier de médias (presentations/<slug>/medias), écrit par l'éditeur et l'import PowerPoint.
mkdir -p "$DEST/data" "$DEST/presentations"
for d in "$DEST"/presentations/*/; do [ -d "$d" ] && mkdir -p "${d}medias"; done

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
systemctl daemon-reload
systemctl enable --now amphi-replay

if [ "$PROXY" = nginx ]; then
  sed "s/__DOMAINE__/$DOMAINE/g" "$DEST/deploy/nginx.conf" > /etc/nginx/sites-available/amphi-replay
  ln -sf /etc/nginx/sites-available/amphi-replay /etc/nginx/sites-enabled/amphi-replay
  nginx -t && systemctl reload nginx
  echo "== certificat Let's Encrypt pour $DOMAINE (le DNS doit déjà pointer vers ce serveur)"
  if certbot --nginx -d "$DOMAINE" --non-interactive --agree-tos --register-unsafely-without-email --redirect; then
    systemctl reload nginx
  else
    echo "!! certbot a échoué (DNS pas encore propagé ?). Relancer plus tard :"
    echo "   certbot --nginx -d $DOMAINE --non-interactive --agree-tos --register-unsafely-without-email --redirect"
  fi
else
  cp "$DEST/deploy/Caddyfile" /etc/caddy/Caddyfile
  mkdir -p /etc/systemd/system/caddy.service.d
  printf '[Service]\nEnvironmentFile=%s\n' "$ENV_FICHIER" > /etc/systemd/system/caddy.service.d/amphi.conf
  systemctl daemon-reload
  systemctl restart caddy
fi

JETON="$(grep '^AMPHI_JETON=' "$ENV_FICHIER" | cut -d= -f2-)"
echo
echo "Installé."
echo "  Participants   : https://$DOMAINE/q"
echo "  Projecteur     : https://$DOMAINE/deck/?role=scene"
echo "  Source OBS     : https://$DOMAINE/deck/?role=obs"
echo "  Télécommande   : https://$DOMAINE/remote?token=$JETON"
echo "  Présentations  : https://$DOMAINE/presentations?token=$JETON"
echo "  Santé          : https://$DOMAINE/sante"
echo "Mettre à jour   : sudo bash $DEST/deploy/deploy.sh"
