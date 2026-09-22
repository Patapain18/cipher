"""
Mesure le coût de la vue baie DANS la vraie WKWebView, fenêtre au premier
plan : deltas de temps CPU (processus WebKit nés avec la fenêtre ET
WindowServer), occupation GPU par ioreg, et compteur d'images.

    /usr/bin/python3 web/outils/mesure_baie.py [--plan 60] [--baie 180] [--fermee 60]

Trois phases : vue plan, vue baie (les événements jouent), refermée.
Le résultat est imprimé en JSON et écrit dans web/outils/mesure_baie.json.

POURQUOI PAS `ps -o %cpu` : c'est une moyenne sur toute la vie du
processus. On mesure des DELTAS de temps CPU cumulé sur une fenêtre de
temps fixe. Et on compte WindowServer : chaque image livrée fait
recomposer la fenêtre, et ce coût-là n'est pas dans les processus
WebKit. Écran verrouillé, WebKit ne dessine pas : la mesure refuse de
partir si le compteur d'images n'avance pas.
"""
import argparse, json, os, re, subprocess as sp, sys, threading, time

ICI = os.path.dirname(os.path.abspath(__file__))
PROJET = os.path.abspath(os.path.join(ICI, "..", ".."))
sys.path.insert(0, os.path.join(PROJET, "web"))

ap = argparse.ArgumentParser()
ap.add_argument("--plan", type=float, default=60)
ap.add_argument("--baie", type=float, default=180)
ap.add_argument("--fermee", type=float, default=60)
ap.add_argument("--port", type=int, default=8770)
A = ap.parse_args()

from AppKit import NSApp
from Foundation import NSOperationQueue
import fenetre_native

TITRE = "Mesure vue baie"

def port_ouvert(port):
    import socket
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0

if not port_ouvert(A.port):
    env = dict(os.environ, DASHBOARD_PARENT_PID=str(os.getpid()), DASHBOARD_PORT=str(A.port))
    sp.Popen(["/usr/bin/python3", "web/server.py"], cwd=PROJET, env=env,
             stdout=sp.DEVNULL, stderr=sp.DEVNULL, start_new_session=True)


def fenetre():
    return next((x for x in NSApp.windows() if x.title() == TITRE), None)


def js(code, delai=8.0):
    boite, pret = {}, threading.Event()
    def sur_principal():
        w = fenetre()
        if not w:
            boite["err"] = "fenêtre introuvable"; return pret.set()
        def fini(res, err):
            boite["res"], boite["err"] = res, (str(err) if err else None); pret.set()
        w.contentView().evaluateJavaScript_completionHandler_(code, fini)
    NSOperationQueue.mainQueue().addOperationWithBlock_(sur_principal)
    pret.wait(delai)
    return boite


def pids_webkit():
    """Les processus WebKit de CETTE vue, par l'API privée de WKWebView."""
    boite, pret = {}, threading.Event()
    def sur_principal():
        w = fenetre()
        try:
            v = w.contentView()
            boite["web"] = int(v._webProcessIdentifier())
            boite["gpu"] = int(v._gpuProcessIdentifier())
        except Exception as e:
            boite["err"] = str(e)
        pret.set()
    NSOperationQueue.mainQueue().addOperationWithBlock_(sur_principal)
    pret.wait(5)
    return boite


def pid_windowserver():
    out = sp.run(["pgrep", "-x", "WindowServer"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def temps_cpu(pid):
    """Temps CPU cumulé, en secondes."""
    out = sp.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    m = re.match(r"(?:(\d+)-)?(?:(\d+):)?(\d+):(\d+)(?:\.(\d+))?", out)
    if not m:
        return None
    j, h, mn, s, cs = m.groups()
    return (int(j or 0) * 86400 + int(h or 0) * 3600 + int(mn) * 60 + int(s)
            + (int(cs) / (10 ** len(cs)) if cs else 0))


def gpu_util():
    out = sp.run(["ioreg", "-r", "-d", "1", "-c", "IOAccelerator"], capture_output=True, text=True).stdout
    m = re.search(r'"Device Utilization %"=(\d+)', out)
    return int(m.group(1)) if m else None


def images():
    r = js("window.__vueBaie ? __vueBaie.images : -1")
    try:
        return int(r.get("res"))
    except Exception:
        return None


def etat_vue():
    r = js("window.__vueBaie ? JSON.stringify({ouverte: __vueBaie.etat.ouverte, images: __vueBaie.images, enCours: __vueBaie.etat.enCours}) : 'sans debug'")
    return r.get("res")


def phase(nom, duree, pids):
    print(f"  {nom:8} début : {etat_vue()}", flush=True)
    t0 = time.time()
    a = {k: temps_cpu(p) for k, p in pids.items()}
    im0 = images()
    gpus = []
    while time.time() - t0 < duree:
        time.sleep(2.0)
        g = gpu_util()
        if g is not None:
            gpus.append(g)
    b = {k: temps_cpu(p) for k, p in pids.items()}
    im1 = images()
    ecoule = time.time() - t0
    res = {"secondes": round(ecoule, 1), "images": (im1 - im0) if (im0 is not None and im1 is not None) else None,
           "gpu_util_moyenne_pct": round(sum(gpus) / len(gpus), 1) if gpus else None}
    for k in pids:
        if a[k] is not None and b[k] is not None:
            res[k + "_pct_coeur"] = round(100 * (b[k] - a[k]) / ecoule, 2)
    print(f"  {nom:8} fin   : {etat_vue()}", flush=True)
    print(f"  {nom:8} {json.dumps(res, ensure_ascii=False)}", flush=True)
    return res


def scenario():
    time.sleep(4)
    for _ in range(60):
        r = js("document.querySelectorAll('#stocks-body tr').length")
        try:
            if r.get("res") is not None and int(r["res"]) > 5:
                break
        except Exception:
            pass
        time.sleep(1)
    else:
        print("!! tableau non chargé", file=sys.stderr); sys.stdout.flush(); os._exit(2)

    def activer():
        NSApp.activateIgnoringOtherApps_(True)
        w = fenetre()
        if w:
            w.makeKeyAndOrderFront_(None)
    NSOperationQueue.mainQueue().addOperationWithBlock_(activer)
    time.sleep(2)

    pw = pids_webkit()
    pids = {"WebContent": pw.get("web"), "GPU": pw.get("gpu"), "WindowServer": pid_windowserver()}
    pids = {k: v for k, v in pids.items() if v}
    print("PIDs :", pids, flush=True)

    # Écran verrouillé ? On force une image et on vérifie que rAF vit.
    # evaluateJavaScript ne sait pas attendre une promesse : on pose un
    # drapeau, on laisse passer une seconde, on le relit.
    js("window.__rafOK = false; requestAnimationFrame(function(){ window.__rafOK = true; }); 0")
    time.sleep(1.2)
    r = js("window.__rafOK ? 'raf vivant' : 'raf mort'")
    print("rAF :", r.get("res"), flush=True)
    if r.get("res") != "raf vivant":
        print("!! requestAnimationFrame ne tire pas (écran verrouillé, fenêtre occultée ?) — mesure refusée", file=sys.stderr)
        sys.stdout.flush(); os._exit(3)

    # La vue s'ouvre seule au chargement (c'est le site) : on la ferme
    # pour mesurer le plan, on la rouvre, on la referme.
    js("window.__vueBaie && __vueBaie.fermer()")
    # 40 s de repos avant la première phase : l'application se pose.
    print("après fermer :", etat_vue(), flush=True)
    print("repos 40 s…", flush=True); time.sleep(40)
    rapport = {"pids": pids, "date": time.strftime("%Y-%m-%d %H:%M")}
    rapport["plan"] = phase("plan", A.plan, pids)

    js("window.__vueBaie && __vueBaie.ouvrir()")
    time.sleep(1.5)
    rapport["baie"] = phase("baie", A.baie, pids)

    js("window.__vueBaie && __vueBaie.fermer()")
    time.sleep(1.5)
    rapport["fermee"] = phase("fermée", A.fermee, pids)

    with open(os.path.join(ICI, "mesure_baie.json"), "w") as f:
        json.dump(rapport, f, indent=1, ensure_ascii=False)
    print(json.dumps(rapport, ensure_ascii=False), flush=True)
    sys.stdout.flush()
    os._exit(0)


threading.Thread(target=scenario, daemon=True).start()
fenetre_native.ouvrir("http://127.0.0.1:%d/?debug=1" % A.port, TITRE, largeur=1440, hauteur=900, port_interne=A.port)
