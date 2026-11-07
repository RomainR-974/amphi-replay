#!/usr/bin/env bash
# Télécharge reveal.js dans deck/vendor/reveal pour que le deck soit servi entièrement par notre serveur
# (aucune dépendance à un CDN pendant la session). Lancé par deploy/install.sh et deploy/deploy.sh.
set -euo pipefail
VERSION="${REVEAL_VERSION:-5.1.0}"
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$RACINE/deck/vendor/reveal"

if [ -f "$DEST/reveal.js" ] && grep -q "\"version\": \"$VERSION\"" "$DEST/VERSION" 2>/dev/null; then
  echo "reveal.js $VERSION déjà présent"
  exit 0
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "téléchargement de reveal.js $VERSION…"
curl -fsSL "https://registry.npmjs.org/reveal.js/-/reveal.js-$VERSION.tgz" -o "$TMP/reveal.tgz"
mkdir -p "$TMP/x" && tar -xzf "$TMP/reveal.tgz" -C "$TMP/x"
rm -rf "$DEST" && mkdir -p "$DEST"
cp -r "$TMP/x/package/dist/." "$DEST/"
cp -r "$TMP/x/package/plugin" "$DEST/plugin"
echo "{\"version\": \"$VERSION\"}" > "$DEST/VERSION"
echo "reveal.js $VERSION installé dans $DEST"
