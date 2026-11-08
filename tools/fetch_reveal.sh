#!/usr/bin/env bash
# Télécharge reveal.js et lottie-web dans deck/vendor/ pour que le deck soit servi entièrement par notre
# serveur (aucune dépendance à un CDN pendant la session). Lancé par deploy/install.sh et deploy/deploy.sh.
set -euo pipefail
VERSION="${REVEAL_VERSION:-5.1.0}"
LOTTIE="${LOTTIE_VERSION:-5.12.2}"
RACINE="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$RACINE/deck/vendor/reveal"
DEST_LOTTIE="$RACINE/deck/vendor/lottie"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ -f "$DEST/reveal.js" ] && grep -q "\"version\": \"$VERSION\"" "$DEST/VERSION" 2>/dev/null; then
  echo "reveal.js $VERSION déjà présent"
else
  echo "téléchargement de reveal.js $VERSION…"
  curl -fsSL "https://registry.npmjs.org/reveal.js/-/reveal.js-$VERSION.tgz" -o "$TMP/reveal.tgz"
  mkdir -p "$TMP/x" && tar -xzf "$TMP/reveal.tgz" -C "$TMP/x"
  rm -rf "$DEST" && mkdir -p "$DEST"
  cp -r "$TMP/x/package/dist/." "$DEST/"
  cp -r "$TMP/x/package/plugin" "$DEST/plugin"
  echo "{\"version\": \"$VERSION\"}" > "$DEST/VERSION"
  echo "reveal.js $VERSION installé dans $DEST"
fi

if [ -f "$DEST_LOTTIE/lottie.min.js" ] && grep -q "\"version\": \"$LOTTIE\"" "$DEST_LOTTIE/VERSION" 2>/dev/null; then
  echo "lottie-web $LOTTIE déjà présent"
else
  echo "téléchargement de lottie-web $LOTTIE…"
  curl -fsSL "https://registry.npmjs.org/lottie-web/-/lottie-web-$LOTTIE.tgz" -o "$TMP/lottie.tgz"
  mkdir -p "$TMP/l" && tar -xzf "$TMP/lottie.tgz" -C "$TMP/l"
  rm -rf "$DEST_LOTTIE" && mkdir -p "$DEST_LOTTIE"
  cp "$TMP/l/package/build/player/lottie.min.js" "$DEST_LOTTIE/"
  echo "{\"version\": \"$LOTTIE\"}" > "$DEST_LOTTIE/VERSION"
  echo "lottie-web $LOTTIE installé dans $DEST_LOTTIE"
fi

# Polices Marianne du Système de Design de l'État (thème « dsfr », deck/themes/dsfr.css).
# Seules les polices sont copiées : le reste du paquet @gouvfr/dsfr n'est pas utilisé.
DSFR="${DSFR_VERSION:-1.15.4}"
DEST_DSFR="$RACINE/deck/vendor/dsfr"
if [ -f "$DEST_DSFR/fonts/Marianne-Regular.woff2" ] && grep -q "\"version\": \"$DSFR\"" "$DEST_DSFR/VERSION" 2>/dev/null; then
  echo "polices Marianne (dsfr $DSFR) déjà présentes"
else
  echo "téléchargement des polices Marianne (@gouvfr/dsfr $DSFR)…"
  curl -fsSL "https://registry.npmjs.org/@gouvfr/dsfr/-/dsfr-$DSFR.tgz" -o "$TMP/dsfr.tgz"
  mkdir -p "$TMP/d" && tar -xzf "$TMP/dsfr.tgz" -C "$TMP/d" --wildcards 'package/dist/fonts/Marianne-*.woff2'
  rm -rf "$DEST_DSFR" && mkdir -p "$DEST_DSFR/fonts"
  for f in Regular Bold Light Medium Regular_Italic; do
    cp "$TMP/d/package/dist/fonts/Marianne-$f.woff2" "$DEST_DSFR/fonts/"
  done
  echo "{\"version\": \"$DSFR\", \"paquet\": \"@gouvfr/dsfr\", \"contenu\": \"polices Marianne uniquement\"}" > "$DEST_DSFR/VERSION"
  echo "polices Marianne installées dans $DEST_DSFR/fonts"
fi
