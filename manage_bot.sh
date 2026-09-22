#!/bin/bash
# Script de gestion du Stock Signal Bot
# Usage:
#   ./manage_bot.sh start   - Lance le bot en arrière-plan
#   ./manage_bot.sh stop    - Arrête le bot
#   ./manage_bot.sh status  - Vérifie si le bot tourne
#   ./manage_bot.sh logs    - Affiche les logs en temps réel
#   ./manage_bot.sh history - Affiche l'historique des signaux

cd "$(dirname "$0")"

BOT_NAME="stock_bot.py"
LOG_FILE="logs/stock_bot_live.log"
PID_FILE="logs/stock_bot.pid"

mkdir -p logs data

case "$1" in
    start)
        # Vérifier si déjà lancé
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "❌ Le bot tourne déjà (PID: $(cat $PID_FILE))"
            exit 1
        fi

        # L'univers n'est plus recopié ici : il vit dans
        # stock_engine.UNIVERSE, et stock_bot.py l'utilise par défaut.
        # Une liste en dur dans ce script aurait continué à lancer le bot
        # sur les neuf anciennes valeurs sans que rien ne le signale.
        # STOCK_TICKERS dans .env reste prioritaire si tu veux le
        # restreindre ponctuellement.
        TICKERS="${STOCK_TICKERS:-}"
        CAPITAL="${STOCK_CAPITAL:-100}"

        if [ -n "$TICKERS" ]; then
            TICKER_ARGS=(--tickers "$TICKERS")
            echo "🚀 Lancement du Stock Signal Bot..."
            echo "   Actions: $TICKERS"
        else
            TICKER_ARGS=()
            echo "🚀 Lancement du Stock Signal Bot..."
            echo "   Actions: univers par défaut (stock_engine.UNIVERSE)"
        fi
        echo "   Capital: ${CAPITAL}€"
        echo ""

        # Lance en arrière-plan. PAS de caffeinate : le Mac doit pouvoir
        # dormir.
        #
        # Le bot tournait auparavant sous `caffeinate -i`, qui empêche la
        # mise en veille. Or un Mac éveillé écran éteint consomme environ
        # 5 à 10 W contre 0,3 W en veille : sur une nuit, cela représente
        # presque un cycle de charge, et quelques centaines de cycles par
        # an d'usure inutile de la batterie.
        #
        # Ce bot n'a rien à y gagner : il émet des signaux qu'on exécute
        # à la main chez son courtier, pas des ordres à la seconde près.
        # Quand le Mac dort, le processus est suspendu et repart au
        # réveil ; les scans manqués n'ont aucune conséquence, seul
        # l'historique a quelques trous.
        nohup python3 "$BOT_NAME" \
            "${TICKER_ARGS[@]}" \
            --capital "$CAPITAL" \
            > "$LOG_FILE" 2>&1 &

        echo $! > "$PID_FILE"
        sleep 2

        if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "✅ Bot lancé (PID: $(cat $PID_FILE))"
            echo "📁 Logs: $LOG_FILE"
            echo ""
            echo "Commandes utiles:"
            echo "  ./manage_bot.sh logs    - Voir les logs en direct"
            echo "  ./manage_bot.sh status  - Voir le statut"
            echo "  ./manage_bot.sh stop    - Arrêter le bot"
            echo "  ./manage_bot.sh history - Voir l'historique"
        else
            echo "❌ Erreur au démarrage. Vérifie $LOG_FILE"
            exit 1
        fi
        ;;

    stop)
        if [ ! -f "$PID_FILE" ]; then
            echo "❌ Aucun bot en cours"
            exit 1
        fi

        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            kill "$PID"
            # Tuer aussi caffeinate
            pkill -f "caffeinate.*stock_bot.py" 2>/dev/null
            rm "$PID_FILE"
            echo "✅ Bot arrêté"
        else
            echo "⚠️  Le PID enregistré ($PID) n'existe plus"
            rm "$PID_FILE"
        fi
        ;;

    status)
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            PID=$(cat "$PID_FILE")
            UPTIME=$(ps -o etime= -p "$PID" | tr -d ' ')
            echo "✅ Bot actif"
            echo "   PID: $PID"
            echo "   Uptime: $UPTIME"
            echo "   Logs: $LOG_FILE"

            # Dernier signal
            if [ -f "data/signal_history.json" ]; then
                LAST=$(python3 -c "
import json
try:
    with open('data/signal_history.json') as f:
        h = json.load(f)
    changes = [x for x in h if x.get('is_change')]
    if changes:
        last = changes[-1]
        print(f\"   Dernier signal: {last['ticker']} → {last['recommendation']} @ \${last['price']:.2f} ({last['timestamp'][:16]})\")
    else:
        total = len(h)
        print(f'   Snapshots enregistrés: {total} (aucun changement de signal pour le moment)')
except Exception as e:
    print(f'   Erreur lecture historique: {e}')
")
                echo "$LAST"
            fi
        else
            echo "❌ Bot inactif"
            [ -f "$PID_FILE" ] && rm "$PID_FILE"
        fi
        ;;

    logs)
        if [ ! -f "$LOG_FILE" ]; then
            echo "❌ Aucun log disponible"
            exit 1
        fi
        echo "📜 Logs en direct (Ctrl+C pour quitter)"
        echo "================================"
        tail -f "$LOG_FILE"
        ;;

    history)
        python3 "$BOT_NAME" --history
        ;;

    test-discord)
        python3 "$BOT_NAME" --test-discord
        ;;

    restart)
        $0 stop
        sleep 2
        $0 start
        ;;

    *)
        echo "Usage: $0 {start|stop|status|logs|history|test-discord|restart}"
        echo ""
        echo "  start         - Lance le bot en arrière-plan (24/7)"
        echo "  stop          - Arrête le bot"
        echo "  status        - Affiche le statut du bot"
        echo "  logs          - Affiche les logs en temps réel"
        echo "  history       - Affiche l'historique des signaux"
        echo "  test-discord  - Test la notification Discord"
        echo "  restart       - Redémarre le bot"
        exit 1
        ;;
esac
