"""
Un faux flux de Yahoo, pilotable, pour vérifier le direct sans dépendre du
marché (outils/verif_direct.mjs). Il parle comme Yahoo (une enveloppe JSON
{"type": "pricing", "message": <base64>}, le message en protobuf
PricingData de yfinance 1.2.0) sur ws://127.0.0.1:<port>, et se pilote par
HTTP sur http://127.0.0.1:<port + 1> :

    /etat                 {"abonnes", "connexions", "envoyes", "muet", "refuse", "seance"}
    /prix?AMD=420.5       fixe un cours (envoyé chaque seconde à qui l'a demandé ; 100 par défaut)
    /seance?v=2           le market_hours des messages (1 = séance normale, 2 = après-séance)
    /muet   /parle        se taire ou reparler (les connexions restent ouvertes)
    /couper               ferme les connexions en cours
    /refuser  /accepter   refuser (503) ou accepter les nouvelles connexions
    /abime                envoie un message illisible à chacun

Il n'écoute que sur 127.0.0.1 et n'appelle personne.

    /usr/bin/python3 outils/faux_flux.py 8790
"""

import base64
import json
import logging
import sys
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore")
from websockets.exceptions import ConnectionClosed     # noqa: E402
from websockets.sync.server import serve                # noqa: E402
from yfinance.pricing_pb2 import PricingData            # noqa: E402

_MUET = logging.getLogger("faux_flux")
_MUET.addHandler(logging.NullHandler())
_MUET.propagate = False

etat = {"prix": {}, "seance": 1, "muet": False, "refuse": False, "coupe": 0,
        "abonnes": set(), "connexions": 0, "envoyes": 0, "abime": 0}
verrou = threading.Lock()


def message(symbole, prix, seance):
    d = PricingData(id=symbole, price=prix, time=int(time.time()) * 1000, market_hours=seance,
                    exchange="NMS", quote_type=8)
    return json.dumps({"type": "pricing", "message": base64.b64encode(d.SerializeToString()).decode()})


def gerer(ws):
    with verrou:
        etat["connexions"] += 1
        coupe, abime = etat["coupe"], etat["abime"]
    abonnes = set()
    prochain = time.time()
    try:
        while True:
            try:
                brut = ws.recv(timeout=0.05)
                demande = json.loads(brut)
                abonnes |= set(demande.get("subscribe", []))
                abonnes -= set(demande.get("unsubscribe", []))
                with verrou:
                    etat["abonnes"] = set(abonnes)
            except TimeoutError:
                pass
            with verrou:
                if etat["coupe"] != coupe:
                    ws.close()
                    return
                muet, seance, prix = etat["muet"], etat["seance"], dict(etat["prix"])
                abimer = etat["abime"] != abime
                abime = etat["abime"]
            if abimer:
                ws.send("ceci n'est pas un message de Yahoo")
            if time.time() >= prochain:
                prochain = time.time() + 1.0
                if not muet:
                    for s in sorted(abonnes):
                        ws.send(message(s, float(prix.get(s, 100.0)), seance))
                        with verrou:
                            etat["envoyes"] += 1
    except ConnectionClosed:
        return


def filtre(connexion, _requete):
    with verrou:
        refuse = etat["refuse"]
    return connexion.respond(503, "fermé pour l'essai\n") if refuse else None


class Pilote(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        q = {k: v[-1] for k, v in parse_qs(url.query).items()}
        with verrou:
            if url.path == "/prix":
                for s, v in q.items():
                    etat["prix"][s] = float(v)
            elif url.path == "/seance":
                etat["seance"] = int(q.get("v", 1))
            elif url.path in ("/muet", "/parle"):
                etat["muet"] = url.path == "/muet"
            elif url.path == "/couper":
                etat["coupe"] += 1
            elif url.path in ("/refuser", "/accepter"):
                etat["refuse"] = url.path == "/refuser"
            elif url.path == "/abime":
                etat["abime"] += 1
            elif url.path != "/etat":
                self.send_error(404)
                return
            corps = json.dumps({"abonnes": sorted(etat["abonnes"]), "connexions": etat["connexions"],
                                "envoyes": etat["envoyes"], "muet": etat["muet"], "refuse": etat["refuse"],
                                "seance": etat["seance"], "prix": etat["prix"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        self.wfile.write(corps)

    def log_message(self, *_a):
        pass


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8790
    serveur = serve(gerer, "127.0.0.1", port, process_request=filtre, logger=_MUET)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    pilote = ThreadingHTTPServer(("127.0.0.1", port + 1), Pilote)
    print(f"faux flux : ws://127.0.0.1:{port}, pilote http://127.0.0.1:{port + 1}", flush=True)
    try:
        pilote.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        serveur.shutdown()


if __name__ == "__main__":
    main()
