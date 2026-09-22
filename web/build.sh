#!/bin/zsh
# ═══════════════════════════════════════════════════════════════
#  build.sh — fabrique « Signaux actions.app »
#
#  L'application est une FENÊTRE, pas une copie du dashboard : elle
#  lance web/server.py depuis ce projet. Le bundle ne contient donc ni
#  pandas, ni les pages — quinze mégaoctets au lieu de trois cents, et
#  surtout une seule version du code.
#
#  ELLE N'EST PAS DISTRIBUABLE, et c'est voulu : elle lit `data/`, que
#  le bot alimente sur cette machine. Envoyée à quelqu'un d'autre, elle
#  ouvrirait une fenêtre sur un dossier qui n'existe pas. C'est le Hub
#  qui se transmet, pas ce dashboard — voir hub/build.sh.
#
#  Usage :  ./web/build.sh
# ═══════════════════════════════════════════════════════════════

set -e
cd "$(dirname "$0")"

NOM="Signaux actions"
VERSION=$(/usr/bin/python3 -c "import re;print(re.search(r'VERSION = \"([^\"]+)\"',open('server.py').read()).group(1))")
echo "▸ $NOM $VERSION"

# L'icône se régénère : aucun binaire d'image à versionner.
[[ -f dashboard.icns ]] || /usr/bin/python3 faire_icone.py

rm -rf build dist "$NOM.spec"

# --hidden-import : AppKit et WebKit sont importés à l'intérieur d'une
# fonction, pour pouvoir échouer proprement sur un système sans PyObjC.
# PyInstaller analyse les imports en tête de fichier : sans ces lignes,
# il ne les embarquerait pas et la fenêtre native serait introuvable une
# fois l'application empaquetée.
#
# Pas de --add-data : les pages sont servies depuis le projet, par le
# serveur. Les embarquer créerait une deuxième copie qui prendrait du
# retard dès la première retouche du dashboard.
/usr/bin/python3 -m PyInstaller \
  --name "$NOM" \
  --windowed \
  --noconfirm \
  --clean \
  --icon dashboard.icns \
  --osx-bundle-identifier com.mathis.signauxactions \
  --hidden-import AppKit \
  --hidden-import WebKit \
  --hidden-import Foundation \
  --hidden-import objc \
  --log-level WARN \
  dashboard_app.py

# PyInstaller écrit une version par défaut : on remet la vraie, pour que
# le Finder et « À propos » disent la même chose que l'application.
/usr/bin/python3 - "$VERSION" "$NOM" <<'PY'
import plistlib, sys
version, nom = sys.argv[1], sys.argv[2]
p = f"dist/{nom}.app/Contents/Info.plist"
with open(p, "rb") as f:
    d = plistlib.load(f)
d["CFBundleShortVersionString"] = version
d["CFBundleVersion"] = version
d["CFBundleName"] = nom
d["CFBundleDisplayName"] = nom
# LSUIElement=False : icône dans le Dock, donc Cmd+Q pour quitter. Sans
# ça, l'application tournerait sans moyen visible de l'arrêter.
d["LSUIElement"] = False
with open(p, "wb") as f:
    plistlib.dump(d, f)
PY

xattr -cr "dist/$NOM.app" 2>/dev/null || true

# build/ n'est que le plan de travail de PyInstaller : cinq mégaoctets
# d'objets intermédiaires qui se recalculent à chaque fois.
rm -rf build

# Sur le Bureau, à côté du Hub : une application qu'il faut aller
# chercher dans dist/ n'est pas une application, c'est un fichier.
if [[ -d "$HOME/Desktop" ]]; then
  rm -rf "$HOME/Desktop/$NOM.app"
  ditto "dist/$NOM.app" "$HOME/Desktop/$NOM.app"
  echo "▸ ~/Desktop/$NOM.app  ($(du -sh "dist/$NOM.app" | cut -f1))"
fi
