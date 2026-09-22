"""
Capture la vue baie DANS la vraie WKWebView — la seule mesure qui vaille.

    /usr/bin/python3 web/outils/capture_baie.py SORTIE.png [options]

      --attente S     secondes entre l'ouverture de la vue et la capture (3)
      --js CODE       du JavaScript exécuté juste avant la capture
                      (ex. "__vueBaie.forcer('avion', 0.5)")
      --debug         ajoute ?debug=1 à l'URL : window.__vueBaie existe
      --plan          capture la vue plan, sans ouvrir la vue baie
      --largeur L --hauteur H   taille de la fenêtre (1440 × 900)

Démarre web/server.py comme le fait dashboard_app.py (le serveur meurt
avec nous grâce à DASHBOARD_PARENT_PID), ouvre la fenêtre, attend les
données, ouvre la vue baie, exécute --js, attend, capture, quitte. Le
JSON d'état (__vueBaie.etat, écart CSS/GL) est imprimé sur la sortie
standard quand --debug est passé.

POURQUOI PAS LE NAVIGATEUR : l'émulation de viewport met la page à
l'échelle, et une couche fixed portant un canvas WebGL n'y survit pas.
On mesure dans la fenêtre qui affichera réellement le dashboard.
"""
import argparse, json, os, subprocess as sp, sys, threading, time

ICI = os.path.dirname(os.path.abspath(__file__))
PROJET = os.path.abspath(os.path.join(ICI, "..", ".."))
sys.path.insert(0, os.path.join(PROJET, "web"))

ap = argparse.ArgumentParser()
ap.add_argument("sortie")
ap.add_argument("--attente", type=float, default=3.0)
ap.add_argument("--js", default="")
ap.add_argument("--debug", action="store_true")
ap.add_argument("--plan", action="store_true")
ap.add_argument("--largeur", type=int, default=1440)
ap.add_argument("--hauteur", type=int, default=900)
ap.add_argument("--port", type=int, default=8770)
ap.add_argument("--lacet", type=float, default=None, help="lacet forcé, en degrés (nécessite --debug)")
A = ap.parse_args()

from AppKit import NSApp
from Foundation import NSOperationQueue
import fenetre_native

TITRE = "Capture vue baie"

# Le serveur, à nous : s'il tourne déjà (Hub, Terminal), on le réutilise.
def port_ouvert(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0

if not port_ouvert(A.port):
    env = dict(os.environ, DASHBOARD_PARENT_PID=str(os.getpid()), DASHBOARD_PORT=str(A.port))
    sp.Popen(["/usr/bin/python3", "web/server.py"], cwd=PROJET, env=env,
             stdout=sp.DEVNULL, stderr=sp.DEVNULL, start_new_session=True)


def js(code, delai=8.0):
    """Exécute du JavaScript sur le fil principal et attend la réponse."""
    boite, pret = {}, threading.Event()

    def sur_principal():
        w = next((x for x in NSApp.windows() if x.title() == TITRE), None)
        if not w:
            boite["err"] = "fenêtre introuvable"
            return pret.set()

        def fini(res, err):
            boite["res"], boite["err"] = res, (str(err) if err else None)
            pret.set()

        w.contentView().evaluateJavaScript_completionHandler_(code, fini)

    NSOperationQueue.mainQueue().addOperationWithBlock_(sur_principal)
    pret.wait(delai)
    return boite


def numero_fenetre():
    from Quartz import CGWindowListCopyWindowInfo, kCGWindowListOptionAll, kCGNullWindowID
    for w in CGWindowListCopyWindowInfo(kCGWindowListOptionAll, kCGNullWindowID):
        if w.get("kCGWindowName") == TITRE and int(w["kCGWindowBounds"]["Width"]) > 500:
            return str(w["kCGWindowNumber"])
    return None


def scenario():
    time.sleep(4)
    # Attendre les données : mesurer une page vide donne 0 partout, et
    # un zéro parfait est un résultat qui se suspecte avant de se fêter.
    for _ in range(60):
        r = js("document.querySelectorAll('#stocks-body tr').length")
        try:
            if r.get("res") is not None and int(r["res"]) > 5:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        print("!! le tableau n'a pas chargé — capture refusée", file=sys.stderr)
        sys.stdout.flush(); os._exit(2)

    def activer():
        NSApp.activateIgnoringOtherApps_(True)
        w = next((x for x in NSApp.windows() if x.title() == TITRE), None)
        if w:
            w.makeKeyAndOrderFront_(None)
    NSOperationQueue.mainQueue().addOperationWithBlock_(activer)
    time.sleep(1)

    # La vue s'ouvre seule au chargement (c'est le site) : on ne clique
    # que si elle ne l'est pas, et --plan la referme.
    r = js("(function(){var h=document.querySelector('.vue3d');return h && !h.hidden ? 'ouverte' : 'fermee'})()")
    if A.plan and r.get("res") == "ouverte":
        js("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))")
        time.sleep(0.8)
    elif not A.plan and r.get("res") != "ouverte":
        js("document.getElementById('vue3d-bouton').click()")
        time.sleep(1.2)
    if A.lacet is not None:
        js("window.__vueBaie && __vueBaie.lacet(%f)" % (A.lacet * 3.14159265 / 180))
        time.sleep(0.5)
    if A.js:
        r = js(A.js)
        print("js :", json.dumps(r, ensure_ascii=False))
    time.sleep(A.attente)

    if A.debug:
        r = js("JSON.stringify({etat: window.__vueBaie && __vueBaie.etat, ecart: window.__vueBaie && __vueBaie.ecart()})")
        print("état :", r.get("res"))
    else:
        r = js("(function(){try{var h=document.querySelector('.vue3d');return JSON.stringify({ouverte:!!h&&!h.hidden})}catch(e){return 'ERR '+e.message}})()")
        print("état :", r.get("res"))
    cons = js("window.__consoleErreurs ? JSON.stringify(window.__consoleErreurs) : 'non capturée'")
    wid = numero_fenetre()
    sp.run(["screencapture", "-x", "-o", "-l" + wid, A.sortie])
    print("capture :", A.sortie, os.path.getsize(A.sortie) if os.path.exists(A.sortie) else "ABSENTE")
    sys.stdout.flush()
    os._exit(0)


threading.Thread(target=scenario, daemon=True).start()
url = "http://127.0.0.1:%d/%s" % (A.port, "?debug=1" if A.debug else "")
fenetre_native.ouvrir(url, TITRE, largeur=A.largeur, hauteur=A.hauteur, port_interne=A.port)
