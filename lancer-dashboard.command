#!/bin/zsh
# ═══════════════════════════════════════════════════════════════
# LANCER-DASHBOARD.COMMAND — ouvre le tableau de bord 📊
#
# Le dashboard est maintenant une application : « Signaux actions »,
# sur le Bureau. Ce fichier ne fait plus que l'ouvrir — il reste là
# pour ceux qui avaient l'habitude de double-cliquer dessus.
#
# Le geste normal, c'est l'icône du Bureau, ou le Hub.
#
# L'application démarre le serveur elle-même et l'arrête quand on la
# ferme. Elle ne fait que LIRE : aucun ordre, aucune clé d'API. Le bot,
# lui, tourne de son côté sous launchd — voir manage_bot.sh.
# ═══════════════════════════════════════════════════════════════

cd "$(dirname "$0")"

APP="$HOME/Desktop/Signaux actions.app"

if [[ -d "$APP" ]]; then
  # `open -a` sur une application déjà lancée l'active au lieu d'en
  # ouvrir une seconde.
  open -a "$APP"
  echo "Signaux actions ouvert ✓  (tu peux fermer cette fenêtre)"
  exit 0
fi

# Pas d'application installée : on retombe sur le serveur seul plutôt
# que d'échouer. `./web/build.sh` la fabrique.
echo "Application introuvable sur le Bureau — démarrage du serveur seul."
echo "Pour l'installer : ./web/build.sh"
if ! lsof -i :8770 >/dev/null 2>&1; then
  nohup /usr/bin/python3 web/server.py >logs/dashboard.log 2>&1 &
  disown
  sleep 2
fi
open "http://localhost:8770"
