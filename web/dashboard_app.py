"""
Signaux actions — l'application de bureau.

CE QU'ELLE EST
    Une fenêtre native et un serveur surveillé. Elle démarre
    `web/server.py` dans le projet, attend que le port réponde, ouvre
    une fenêtre dessus, et arrête le serveur quand on la ferme.

POURQUOI ELLE N'EMBARQUE PAS LE CODE DU DASHBOARD
    Deux raisons, et la seconde est la vraie.

    D'abord le poids : le serveur importe pandas, numpy, yfinance et ta
    pour calculer les signaux. Empaquetés, ils pèsent quelques centaines
    de mégaoctets — pour une application qui, de toute façon, ne peut
    tourner que sur CETTE machine.

    Ensuite l'unicité : le dashboard lit `data/`, que le bot alimente en
    parallèle. L'application est une VUE sur le projet, pas une copie.
    Modifier une page du dashboard se voit à la prochaine ouverture,
    sans reconstruire quoi que ce soit — et il n'existe jamais deux
    versions du calcul qui pourraient diverger.

    Elle dépend donc du projet et de /usr/bin/python3 : exactement les
    deux choses dont le bot dépend déjà pour tourner sous launchd.

CE QU'ELLE N'ARRÊTE PAS
    Le bot. Il est lancé par launchd, scanne toutes les cinq minutes et
    écrit l'historique. Fermer cette fenêtre ferme une vue, pas la
    collecte.

LANCEMENT
    python3 web/dashboard_app.py          (ou l'icône du Dock)
"""

import json
import os
import socket
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import fenetre_native

TITRE = "Signaux actions"
PORT = int(os.getenv("DASHBOARD_PORT", "8770"))

# /usr/bin/python3 et pas « python3 » : c'est l'interpréteur qui porte
# pandas et yfinance sur cette machine, et celui que launchd utilise
# déjà pour le bot. Celui du PATH peut être un autre (Homebrew, un
# environnement virtuel) où les dépendances n'existent pas — l'écart
# ne se verrait qu'au premier tableau vide.
PYTHON = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else "python3"

ATTENTE_DEMARRAGE = 25.0   # premier import de pandas : quelques secondes


def dossier_projet():
    """
    Où est le projet ?

    Empaquetée, l'application est ailleurs sur le disque : elle ne peut
    pas le déduire de son propre emplacement. On demande donc, dans
    l'ordre : la variable d'environnement (pour qui range son projet
    ailleurs), puis l'emplacement habituel, puis — hors bundle — le
    dossier parent de ce fichier, qui est le bon quand on lance à la
    main depuis le projet.
    """
    pistes = [os.environ.get("TRADING_BOT_DIR"),
              os.path.expanduser("~/test2/trading-bot")]
    if not getattr(sys, "frozen", False):
        pistes.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for piste in pistes:
        if piste and os.path.isfile(os.path.join(piste, "web", "server.py")):
            return piste
    return None


def port_ouvert():
    try:
        with socket.create_connection(("127.0.0.1", PORT), 0.2):
            return True
    except OSError:
        return False


def alerte(message):
    """
    Une application sans terminal ne peut pas se plaindre dans le vide :
    fermée aussitôt ouverte, elle passerait pour cassée sans raison.
    """
    sys.stderr.write(message + "\n")
    try:
        subprocess.run(["/usr/bin/osascript", "-e",
                        f'display dialog {json.dumps(message)} '
                        f'with title {json.dumps(TITRE)} '
                        f'buttons {{"OK"}} default button 1 with icon caution'],
                       capture_output=True, timeout=120)
    except Exception:
        pass


def main():
    projet = dossier_projet()
    if not projet:
        alerte("Le projet est introuvable.\n\n"
               "Cette application affiche les données de ~/test2/trading-bot. "
               "Si le dossier est ailleurs, indique-le avec la variable "
               "TRADING_BOT_DIR.")
        return 1

    url = f"http://localhost:{PORT}"

    # Un serveur déjà en marche — lancé par le Hub, ou au Terminal —
    # n'a pas à être doublé, ni à être arrêté par la fermeture de cette
    # fenêtre : on ne coupe pas ce qu'on n'a pas allumé.
    if port_ouvert():
        fenetre_native.ouvrir(url, TITRE, largeur=1440, hauteur=900,
                              port_interne=PORT)
        return 0

    # start_new_session : le serveur ne reçoit pas le Ctrl+C destiné à
    # cette application. C'est nous qui décidons de son arrêt, plus bas.
    # DASHBOARD_PARENT_PID : le serveur surveille ce PID et s'arrête
    # de lui-même si l'application disparaît. Sans ce garde-fou, un
    # Cmd+Q ou un SIGTERM du Hub laisserait un serveur orphelin occuper
    # le port 8770 — le Hub afficherait « en marche » sans plus rien
    # pour l'arrêter.
    env = dict(os.environ, DASHBOARD_PARENT_PID=str(os.getpid()))
    proc = subprocess.Popen(
        [PYTHON, "web/server.py"], cwd=projet, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )

    debut = time.time()
    while time.time() - debut < ATTENTE_DEMARRAGE and not port_ouvert():
        if proc.poll() is not None:
            erreur = (proc.stderr.read() or b"").decode(errors="replace")
            alerte("Le serveur du dashboard n'a pas démarré.\n\n"
                   + (erreur.strip()[-600:] or "Aucun message d'erreur."))
            return 1
        time.sleep(0.1)

    if not port_ouvert():
        proc.terminate()
        alerte(f"Le serveur n'a pas répondu en {int(ATTENTE_DEMARRAGE)} s.")
        return 1

    def fermer():
        # SIGTERM, jamais SIGKILL : le serveur doit pouvoir finir
        # l'écriture en cours. Le carnet de positions est un JSON — tué
        # net au mauvais moment, il resterait tronqué.
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            pass

    # 1440 et non la valeur par défaut : le tableau des 49 valeurs
    # mesure 1 373 px, gouttières comprises il tient à partir de
    # 1 421 px. En dessous, deux colonnes — « Ce qui pèse » et « Zone
    # probable à 5 j » — restent hors champ et l'en-tête cesse de
    # coller. Ouvrir plus petit, c'est ouvrir sur un tableau amputé.
    fenetre_native.ouvrir(url, TITRE, largeur=1440, hauteur=900,
                          port_interne=PORT, a_la_fermeture=fermer)
    fermer()   # filet : la fenêtre peut rendre la main sans passer par le délégué
    return 0


if __name__ == "__main__":
    # Pas de gestionnaire SIGTERM ici, et c'est délibéré : pendant que
    # la boucle graphique tient le fil principal, Python ne reprend
    # jamais la main pour l'exécuter. Le signal resterait en attente et
    # l'application refuserait de mourir. On laisse donc le comportement
    # par défaut — arrêt immédiat — et c'est le serveur qui remarque
    # notre disparition (voir surveiller_parent dans server.py).
    sys.exit(main())
