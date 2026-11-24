"""
Le cours en direct — le flux de Yahoo, écouté par le serveur du tableau de bord.

CE QUE ÇA FAIT
    Le serveur (web/server.py) garde UNE connexion ouverte au flux en
    direct de Yahoo (wss://streamer.finance.yahoo.com/?version=2) :
    environ un message par seconde et par valeur, avec le dernier cours
    et son heure (essai du 24/11/2026, fin de BRIEF.md). Il en garde le
    dernier cours de chaque valeur, en mémoire. « Ma position »
    (ma_position.cours_actuel) et le tableau des 49 (/api/valeurs/direct)
    y lisent leurs cours, sans appeler Yahoo à chaque fois.

CE QUE ÇA NE FAIT PAS
    Aucun ordre, aucune clé, rien d'écrit sur le disque, et JAMAIS rien
    sur la sortie d'erreur (l'appli Mac la reçoit dans un tuyau qu'elle ne
    vide plus : plein, il bloquerait le serveur). La dernière erreur se
    lit dans etat(). Le flux n'est pas officiel : s'il se tait, se coupe
    ou envoie n'importe quoi, l'appli reprend les barres d'une minute (le
    système d'avant), et le dit.

LES RÈGLES (plan validé par Mathis le 24/11/2026, fin de BRIEF.md)
    - Il ne sert qu'au serveur : activer() est appelé par web/server.py.
      La ligne de commande, le bot, les tests et les mesures ne
      l'activent jamais : cours() leur rend toujours None.
    - Le flux tourne tant qu'un client (la position, le tableau) a
      demandé des symboles depuis moins de DEMANDE_S, et que la Bourse de
      New York n'est pas « fermee » (la nuit, le week-end, un jour
      férié). Il suit l'union des symboles demandés.
    - Les symboles sont redemandés toutes les BATTEMENT_S (comme
      yfinance), et une connexion muette pendant MUET_S est refaite.
    - Un message est gardé seulement s'il est lisible, d'un symbole
      demandé, avec un prix fini et positif, une heure ni absente, ni
      plus de FUTUR_S dans le futur, ni plus ancienne que le dernier
      message gardé du même symbole. Le reste est compté, jamais levé.
    - Toutes les heures sont celles de l'horloge réelle (time.time) : sur
      ce Mac, l'horloge monotone s'arrête pendant la veille.
    - Après une panne : un nouvel essai ATTENTE_S plus tard ; REPRISE_S
      seulement après une connexion qui a donné des cours.

LANCER
    python3 cours_direct.py --verifier   # les cas, avec un faux Yahoo local (aucun réseau)
    python3 cours_direct.py --sonder     # 3 minutes du vrai flux : SPY, AMD, TSLA
    python3 cours_direct.py --sonder 60 SPY NVDA
"""

import base64
import json
import logging
import math
import os
import re
import sys
import threading
import time
from collections import namedtuple
from datetime import datetime, timezone

URL_YAHOO = "wss://streamer.finance.yahoo.com/?version=2"

FRAIS_S = 30            # un cours du flux de moins de 30 s (à l'heure du cours) se prend sans lire les barres
DEMANDE_S = 90          # le flux reste ouvert tant qu'une demande a moins de 90 s
BATTEMENT_S = 15        # les symboles sont redemandés toutes les 15 s (le « battement » de yfinance)
MUET_S = 120            # aucun message gardé pendant 2 minutes : la connexion est refaite
ATTENTE_S = 30          # entre deux essais ratés
REPRISE_S = 2           # le premier nouvel essai après une connexion qui a donné des cours
FUTUR_S = 10            # une heure de cours plus de 10 s dans le futur est refusée
TAILLE_MAX = 64 * 1024  # un message plus gros fait fermer la connexion (websockets), qui est refaite
RECV_S = 1.0            # le fil regarde l'heure au moins une fois par seconde
OUVERTURE_S = 10        # une connexion qui ne s'ouvre pas en 10 s est abandonnée
SEANCE_NORMALE = 1      # market_hours de Yahoo : 0 avant-séance, 1 séance normale, 2 après-séance
ETATS_BOURSE = ("avant", "ouverte", "apres", "fermee", None)

# Un cours du flux : le prix (en dollars), son heure (UTC) et la séance de Yahoo (market_hours).
CoursDirect = namedtuple("CoursDirect", "prix heure seance")

# Les noms de Yahoo : AMD, BRK-B, EURUSD=X, ^GSPC, MC.PA…
_SYMBOLE = re.compile(r"^[A-Z0-9^][A-Z0-9.=\-^]{0,11}$")

# websockets écrit ses avertissements par le module logging : sans journal à lui, le « dernier
# recours » de logging les enverrait sur la sortie d'erreur.
_MUET = logging.getLogger("cours_direct.websockets")
_MUET.addHandler(logging.NullHandler())
_MUET.propagate = False
_MUET.setLevel(logging.CRITICAL + 1)

_verrou = threading.Lock()
_arret = threading.Event()      # les tests arrêtent le fil
_maintenant = time.time         # l'heure réelle ; remplaçable par les tests (la veille du Mac)

_actif = {"url": None}
_demandes = {}                  # client → (instant de la demande, frozenset de symboles)
_bourse = {"etat": None}
_derniers = {}                  # symbole → CoursDirect (le dernier message gardé, toutes séances)
_derniers_normale = {}          # symbole → CoursDirect (le dernier de la séance normale : le tableau des 49)
_fil = {"vivant": False, "objet": None}
_etat = {}


def _remettre_a_zero():
    _demandes.clear()
    _derniers.clear()
    _derniers_normale.clear()
    _bourse["etat"] = None
    _etat.clear()
    _etat.update(etat="arrete", depuis=None, raison=None, messages=0, connexions=0, ignores={},
                 premier_essai=False)


_remettre_a_zero()


# ══════════════════════════════════════════════════════════════════
#  CE QUE LIT LE SERVEUR
# ══════════════════════════════════════════════════════════════════

def activer(url=None):
    """Le serveur active le flux (sans l'ouvrir : il s'ouvre à la première demande). FLUX_URL pour un faux Yahoo."""
    url = url or os.environ.get("FLUX_URL") or URL_YAHOO
    if not url.startswith(("ws://", "wss://")):
        raise ValueError(f"adresse du flux illisible : {url[:80]}")
    _actif["url"] = url


def actif():
    return bool(_actif["url"])


def suivre(client, symboles):
    """
    Ce que veut un client du flux (« position », « tableau ») : ses
    symboles, maintenant. Le fil démarre s'il ne tourne pas, que la
    Bourse n'est pas fermée et que quelqu'un veut un symbole.
    """
    if not _actif["url"]:
        return
    propres = frozenset(s for s in symboles if isinstance(s, str) and _SYMBOLE.match(s))
    with _verrou:
        _demandes[client] = (_maintenant(), propres)
        if _fil["vivant"] or _etat["etat"] == "indisponible":
            return
        if not _voulus() or _bourse["etat"] == "fermee":
            return
        _fil["vivant"] = True
        _changer("connexion", None)
        _etat["premier_essai"] = True
        fil = threading.Thread(target=_boucle, name="cours-direct", daemon=True)
        _fil["objet"] = fil
    fil.start()


def bourse(etat):
    """L'état de la Bourse de New York, d'après Yahoo (papier.etat_bourse) : le flux ne s'ouvre pas quand elle est fermée."""
    if etat not in ETATS_BOURSE:
        etat = None
    with _verrou:
        _bourse["etat"] = etat


def cours(symbole):
    """
    Le dernier cours gardé du flux pour ce symbole (CoursDirect), quel
    que soit son âge, ou None (flux pas activé, ou rien reçu). C'est à
    l'appelant de juger sa fraîcheur, par l'heure du cours.
    """
    if not _actif["url"]:
        return None
    with _verrou:
        return _derniers.get(symbole)


def cours_normal(symbole):
    """Le dernier cours gardé de la SÉANCE NORMALE (market_hours 1), quel que soit son âge, ou None :
    un message d'avant- ou d'après-séance ne le remplace pas (le tableau des 49, relecture du 24/11)."""
    if not _actif["url"]:
        return None
    with _verrou:
        return _derniers_normale.get(symbole)


def etat():
    """L'état du flux, pour la page et pour comprendre une panne : jamais d'exception."""
    with _verrou:
        e = {k: (dict(v) if isinstance(v, dict) else v) for k, v in _etat.items()}
    e["actif"] = bool(_actif["url"])
    e["depuis_s"] = None
    if e.get("depuis"):
        e["depuis_s"] = round(_maintenant() - e["depuis"], 1)
        e["depuis"] = datetime.fromtimestamp(e["depuis"], timezone.utc).isoformat(timespec="seconds")
    return e


def rythme_rapide(en_direct):
    """
    La page relit-elle toutes les 2 s (sinon toutes les 60 s) ? Oui en
    direct, pendant la PREMIÈRE connexion après un démarrage, et pendant
    les 10 s qui suivent une ouverture (le temps que les premiers cours
    arrivent). Pas pendant les nouveaux essais après une panne : un flux
    qui accepte la connexion sans répondre faisait relire la page toutes
    les 2 s sous « toutes les minutes » (relecture du 24/11/2026).
    """
    e = etat()
    vient_d_ouvrir = e.get("etat") == "ouvert" and e.get("depuis_s") is not None and e["depuis_s"] < 10
    premiere_connexion = e.get("etat") == "connexion" and bool(e.get("premier_essai"))
    return bool(en_direct or premiere_connexion or vient_d_ouvrir)


def arreter():
    """Arrête le fil et oublie tout (les tests). Le flux reste activé ou non comme avant."""
    _arret.set()
    fil = _fil.get("objet")
    if fil is not None and fil.is_alive() and fil is not threading.current_thread():
        fil.join(timeout=15)
    with _verrou:
        _fil["vivant"] = False
        _fil["objet"] = None
        _remettre_a_zero()
    _arret.clear()


# ══════════════════════════════════════════════════════════════════
#  LE FIL DU FLUX
# ══════════════════════════════════════════════════════════════════

def _voulus():
    """L'union des symboles demandés depuis moins de DEMANDE_S (sous le verrou)."""
    maintenant = _maintenant()
    voulus = set()
    for instant, symboles in _demandes.values():
        if 0 <= maintenant - instant < DEMANDE_S:
            voulus |= symboles
    return frozenset(voulus)


def _changer(nom, raison):
    """Change l'état du flux (sous le verrou)."""
    if _etat["etat"] != nom:
        _etat["depuis"] = _maintenant()
    _etat["etat"] = nom
    _etat["raison"] = raison


def _ignorer(pourquoi):
    with _verrou:
        _etat["ignores"][pourquoi] = _etat["ignores"].get(pourquoi, 0) + 1
    return False


def _garder(brut, abonnes, PricingData):
    """Un message du flux : gardé (True) ou ignoré et compté (False). Jamais d'exception."""
    try:
        if isinstance(brut, (bytes, bytearray)):
            brut = bytes(brut).decode("utf-8")
        enveloppe = json.loads(brut)
        code = enveloppe["message"]
        if not isinstance(code, str):
            return _ignorer("illisible")
        d = PricingData()
        d.ParseFromString(base64.b64decode(code, validate=True))
        symbole, prix, heure_ms, seance = d.id, float(d.price), int(d.time), int(d.market_hours)
    except Exception:
        return _ignorer("illisible")
    if symbole not in abonnes:
        return _ignorer("symbole")
    if not math.isfinite(prix) or prix <= 0:
        return _ignorer("prix")
    heure_s = heure_ms / 1000.0
    if not heure_s > 0 or heure_s > _maintenant() + FUTUR_S:
        return _ignorer("heure")
    heure = datetime.fromtimestamp(heure_s, timezone.utc)
    with _verrou:
        avant = _derniers.get(symbole)
        if avant is not None and heure < avant.heure:
            _etat["ignores"]["recule"] = _etat["ignores"].get("recule", 0) + 1
            return False
        # À la même seconde, le message suivant est le plus récent : il remplace le précédent.
        _derniers[symbole] = CoursDirect(prix, heure, seance)
        if seance == SEANCE_NORMALE:
            _derniers_normale[symbole] = _derniers[symbole]
        _etat["messages"] += 1
    return True


def _en_francais(e):
    """Une erreur de connexion, dite en français (elle s'affiche dans etat())."""
    if isinstance(e, TimeoutError):
        return "Yahoo ne répond pas (délai dépassé)"
    try:
        from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
        if isinstance(e, InvalidStatus):
            return f"Yahoo refuse la connexion (code {e.response.status_code})"
        if isinstance(e, ConnectionClosed):
            codes = {getattr(e.sent, "code", None), getattr(e.rcvd, "code", None)}
            return "la connexion a été fermée" + (" (message trop gros)" if 1009 in codes else "")
        if isinstance(e, InvalidHandshake):
            return "la connexion avec Yahoo n'a pas pu s'établir"
    except Exception:
        pass
    if isinstance(e, OSError):
        return f"réseau indisponible ({e.strerror or type(e).__name__})"
    return f"{type(e).__name__} : {e}"[:200]


def _une_connexion():
    """
    Une connexion, du début à la fin. Rend (issue, a_garde) : « fin » (plus
    personne ne demande, Bourse fermée, arrêt), « panne », ou « indisponible »
    (bibliothèque absente ou changée). Jamais d'exception.
    """
    try:
        from websockets.sync.client import connect
        from yfinance.pricing_pb2 import PricingData
    except Exception as e:
        with _verrou:
            _changer("indisponible", f"bibliothèque absente ou changée : {e}"[:300])
        return "indisponible", False
    a_garde = False
    with _verrou:
        _changer("connexion", None)
        _etat["connexions"] += 1
        url = _actif["url"]
    try:
        with connect(url, open_timeout=OUVERTURE_S, close_timeout=2, max_size=TAILLE_MAX,
                     ping_interval=20, ping_timeout=20, logger=_MUET) as ws:
            with _verrou:
                _changer("ouvert", None)
            abonnes, battement, dernier = frozenset(), 0.0, _maintenant()
            while True:
                with _verrou:
                    voulus = _voulus()
                    fin = _arret.is_set() or not voulus or _bourse["etat"] == "fermee"
                if fin:
                    return "fin", a_garde
                maintenant = _maintenant()
                if voulus != abonnes:
                    partis = abonnes - voulus
                    if partis:
                        ws.send(json.dumps({"unsubscribe": sorted(partis)}))
                    ws.send(json.dumps({"subscribe": sorted(voulus)}))
                    abonnes, battement = voulus, maintenant
                elif maintenant - battement >= BATTEMENT_S:
                    ws.send(json.dumps({"subscribe": sorted(abonnes)}))
                    battement = maintenant
                try:
                    brut = ws.recv(timeout=RECV_S)
                except TimeoutError:
                    brut = None
                if brut is not None and _garder(brut, abonnes, PricingData):
                    a_garde, dernier = True, _maintenant()
                if _maintenant() - dernier > MUET_S:
                    with _verrou:
                        _changer("attente", f"le flux est resté muet {MUET_S:g} s : nouvelle connexion")
                    return "panne", a_garde
    except Exception as e:
        with _verrou:
            _changer("attente", _en_francais(e))
        return "panne", a_garde


def _boucle():
    """Le fil du flux : une connexion à la fois, tant qu'on la demande. Il ne meurt jamais sur une erreur."""
    attente = 0.0
    while True:
        try:
            if attente:
                _arret.wait(attente)
            with _verrou:
                if _arret.is_set() or not _voulus() or _bourse["etat"] == "fermee":
                    _fil["vivant"] = False
                    if _etat["etat"] != "indisponible":
                        _changer("arrete", None)
                    return
            issue, a_garde = _une_connexion()
            with _verrou:
                _etat["premier_essai"] = False
            if issue == "indisponible":
                with _verrou:
                    _fil["vivant"] = False
                return
            attente = 0.0 if issue == "fin" else (REPRISE_S if a_garde else ATTENTE_S)
        except Exception as e:        # un filet : rien ne doit sortir de ce fil
            with _verrou:
                _changer("attente", _en_francais(e))
            attente = ATTENTE_S


# ══════════════════════════════════════════════════════════════════
#  LA SONDE (en vrai, pendant une séance)
# ══════════════════════════════════════════════════════════════════

def sonder(duree=180, symboles=("SPY", "AMD", "TSLA")):
    """Le vrai flux pendant `duree` secondes : chaque 10 s, le dernier cours de chaque symbole et son âge."""
    import warnings
    warnings.filterwarnings("ignore")
    activer(URL_YAHOO)
    debut = time.time()
    while time.time() - debut < duree:
        suivre("sonde", symboles)
        time.sleep(10)
        e = etat()
        lignes = []
        for s in symboles:
            c = cours(s)
            lignes.append(f"{s} {c.prix:.4f} il y a {time.time() - c.heure.timestamp():4.1f} s (séance {c.seance})"
                          if c else f"{s} —")
        print(f"{time.strftime('%H:%M:%S')}  {e['etat']:<10} {e['messages']:5} messages  " + " | ".join(lignes),
              flush=True)
    e = etat()
    print(f"\nFin : {e['messages']} messages gardés, {e['connexions']} connexion(s), ignorés {e['ignores']}, "
          f"dernière raison : {e['raison'] or 'aucune'}")
    arreter()


# ══════════════════════════════════════════════════════════════════
#  LES VÉRIFICATIONS (un faux Yahoo local, aucun réseau)
# ══════════════════════════════════════════════════════════════════

def verifier():
    import queue
    import tempfile
    import warnings
    warnings.filterwarnings("ignore")
    from websockets.exceptions import ConnectionClosed
    from websockets.sync.server import serve
    from yfinance.pricing_pb2 import PricingData

    global DEMANDE_S, BATTEMENT_S, MUET_S, ATTENTE_S, REPRISE_S, RECV_S, OUVERTURE_S, _maintenant
    reglages = (DEMANDE_S, BATTEMENT_S, MUET_S, ATTENTE_S, REPRISE_S, RECV_S, _maintenant, OUVERTURE_S)
    resultats = []

    def ok(nom, cond):
        resultats.append((nom, bool(cond)))
        print(("  ok  " if cond else "  NON ") + nom, flush=True)

    def attendre_que(cond, delai=4.0, demande=None):
        """Attend une condition ; `demande` = (client, symboles) est redemandée en attendant (sinon elle expire)."""
        fin = time.time() + delai
        while time.time() < fin:
            if demande:
                suivre(*demande)
            if cond():
                return True
            time.sleep(0.01)
        return cond()

    AMD = ("position", ["AMD"])

    def message(symbole, prix, heure_s=None, seance=1):
        d = PricingData(id=symbole, price=prix, time=int(round((time.time() if heure_s is None else heure_s) * 1000)),
                        market_hours=seance)
        return json.dumps({"type": "pricing", "message": base64.b64encode(d.SerializeToString()).decode()})

    class FauxYahoo:
        """Un faux flux local : il note les demandes reçues et envoie ce qu'on lui donne."""
        def __init__(self):
            self.a_envoyer = queue.Queue()
            self.recus = []
            self.connexions = 0
            self.refuser = False
            self.serveur = serve(self._gerer, "127.0.0.1", 0, process_request=self._filtre, logger=_MUET)
            self.port = self.serveur.socket.getsockname()[1]
            threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

        def _filtre(self, connexion, requete):
            if self.refuser:
                return connexion.respond(503, "fermé pour l'essai\n")
            return None

        def _gerer(self, ws):
            self.connexions += 1
            while True:
                try:
                    self.recus.append(json.loads(ws.recv(timeout=0.01)))
                except TimeoutError:
                    pass
                except ConnectionClosed:
                    return
                try:
                    quoi = self.a_envoyer.get_nowait()
                except queue.Empty:
                    continue
                if quoi == "fermer":
                    ws.close()
                    return
                try:
                    ws.send(quoi)
                except ConnectionClosed:
                    return

        def envoyer(self, *messages):
            for m in messages:
                self.a_envoyer.put(m)

        def demandes(self, cle):
            return [r[cle] for r in self.recus if isinstance(r, dict) and cle in r]

        def fermer(self):
            self.serveur.shutdown()

    # La sortie d'erreur est captée pendant TOUS les cas : rien ne doit y être écrit.
    sortie_avant = os.dup(2)
    capte = tempfile.TemporaryFile()
    os.dup2(capte.fileno(), 2)
    faux = FauxYahoo()
    try:
        # Des délais courts, pour que les cas tiennent en quelques secondes.
        DEMANDE_S, BATTEMENT_S, MUET_S, ATTENTE_S, REPRISE_S, RECV_S = 30.0, 0.3, 30.0, 2.0, 0.1, 0.05
        url = f"ws://127.0.0.1:{faux.port}"

        _MUET.error("essai : ce message du journal de websockets ne doit aller nulle part")
        print("Avant activer()")
        arreter()
        _actif["url"] = None
        suivre("position", ["AMD", "SPY"])
        time.sleep(0.2)
        ok("pas activé : suivre() n'ouvre aucune connexion", faux.connexions == 0 and not _fil["vivant"])
        ok("pas activé : cours() rend None", cours("AMD") is None)
        for mauvaise in ("http://exemple", "", "ftp://x"):
            try:
                activer(mauvaise) if mauvaise else None
                refuse = mauvaise == ""
            except ValueError:
                refuse = True
            ok(f"activer({mauvaise!r}) : refusé, ou l'adresse par défaut", refuse)
        _actif["url"] = None

        print("Un flux normal")
        activer(url)
        ok("activer() n'ouvre rien tout seul", faux.connexions == 0)
        vraie_boucle, retenu = globals()["_boucle"], threading.Event()
        globals()["_boucle"] = lambda: (retenu.wait(5), vraie_boucle())
        suivre("position", ["AMD"])
        ok("dès la demande, avant que le fil ne tourne, l'état dit « connexion » (la page relit vite)",
           etat()["etat"] == "connexion" and rythme_rapide(False) is True)
        retenu.set()
        globals()["_boucle"] = vraie_boucle
        suivre("position", ["AMD", "SPY", "pas un symbole !", None, "EURUSD=X"])
        ok("la connexion s'ouvre à la première demande", attendre_que(lambda: etat()["etat"] == "ouvert"))
        e = etat()
        ok("l'état dit depuis quand il est ouvert (en secondes, et en heure UTC)",
           e["depuis_s"] is not None and 0 <= e["depuis_s"] < 5 and e["depuis"].endswith("+00:00"))
        ok("juste ouvert, sans cours frais : la page relit vite (les 10 premières secondes)",
           rythme_rapide(False) is True)
        decalage_ouvert = [11.0]
        _maintenant = lambda: time.time() + decalage_ouvert[0]     # noqa: E731
        ok("… plus à 11 s, sans cours frais", rythme_rapide(False) is False and rythme_rapide(True) is True)
        _maintenant = reglages[6]
        ok("les symboles demandés (les noms illisibles écartés)",
           attendre_que(lambda: ["AMD", "EURUSD=X", "SPY"] in faux.demandes("subscribe")))
        t0 = time.time() - 1
        faux.envoyer(message("AMD", 410.42, t0, 1))
        ok("un message lisible est gardé", attendre_que(lambda: cours("AMD") is not None))
        c = cours("AMD")
        ok("le prix (32 bits, comme Yahoo), l'heure en UTC et la séance",
           c and abs(c.prix - 410.42) < 1e-4 and c.heure.tzinfo is not None
           and abs(c.heure.timestamp() - round(t0 * 1000) / 1000) < 1e-6 and c.seance == 1)
        ok("un symbole jamais reçu : None", cours("SPY") is None)

        print("Les messages à ignorer")
        avant = cours("AMD")

        def ignore(nom, brut, pourquoi):
            n0 = etat()["ignores"].get(pourquoi, 0)
            faux.envoyer(brut)
            vu = attendre_que(lambda: etat()["ignores"].get(pourquoi, 0) == n0 + 1)
            ok(f"ignoré ({pourquoi}) : {nom}", vu and cours("AMD") == avant)

        maintenant = time.time()
        ignore("pas du JSON", "ceci n'est pas du JSON", "illisible")
        ignore("du JSON sans « message »", json.dumps({"type": "pricing"}), "illisible")
        ignore("un message qui n'est pas du texte", json.dumps({"message": 12}), "illisible")
        ignore("un base64 illisible", json.dumps({"message": "@@@pas du base64@@@"}), "illisible")
        ignore("un protobuf illisible", json.dumps({"message": base64.b64encode(b"\xff\xff\xff\xff").decode()}),
               "illisible")
        ignore("un JSON qui est une liste", json.dumps([1, 2]), "illisible")
        ignore("un symbole pas demandé", message("MSFT", 300.0, maintenant), "symbole")
        ignore("un prix NaN", message("AMD", float("nan"), maintenant), "prix")
        ignore("un prix infini", message("AMD", float("inf"), maintenant), "prix")
        ignore("un prix nul", message("AMD", 0.0, maintenant), "prix")
        ignore("un prix négatif", message("AMD", -5.0, maintenant), "prix")
        ignore("une heure absente (0)", message("AMD", 411.0, 0), "heure")
        ignore("une heure négative", message("AMD", 411.0, -100), "heure")
        ignore("une heure 60 s dans le futur", message("AMD", 411.0, maintenant + 60), "heure")
        ignore("une heure plus ancienne que la dernière gardée", message("AMD", 409.0, t0 - 5), "recule")
        faux.envoyer(message("SPY", 712.5, maintenant + FUTUR_S - 2))
        ok("une heure 8 s dans le futur (l'horloge de Yahoo) est gardée",
           attendre_que(lambda: cours("SPY") is not None and cours("SPY").prix == 712.5))
        deja = cours("SPY")
        faux.envoyer(message("SPY", 712.75, deja.heure.timestamp()))
        ok("à la même seconde, le message suivant remplace le précédent",
           attendre_que(lambda: cours("SPY").prix == 712.75))
        ok("le fil est toujours vivant après tout ça", _fil["vivant"] and etat()["etat"] == "ouvert")

        print("Le cours de la séance normale (le tableau des 49)")
        t1 = cours("AMD").heure.timestamp() + 0.001  # juste après le dernier gardé, jamais dans le futur
        faux.envoyer(message("AMD", 430.0, t1, 1))
        ok("un message de séance normale : le dernier cours ET le cours de séance",
           attendre_que(lambda: cours("AMD").prix == 430.0) and cours_normal("AMD").prix == 430.0)
        faux.envoyer(message("AMD", 431.5, t1 + 0.001, 2))
        ok("un message d'après-séance remplace le dernier cours, PAS celui de la séance normale",
           attendre_que(lambda: cours("AMD").prix == 431.5) and cours_normal("AMD").prix == 430.0
           and cours("AMD").seance == 2)
        faux.envoyer(message("AMD", 432.0, t1 + 0.002, 1))
        ok("un nouveau message de séance normale le remplace",
           attendre_que(lambda: cours_normal("AMD").prix == 432.0))
        ok("un symbole sans message de séance normale : None", cours_normal("MSFT") is None)

        print("Le battement et les symboles")
        n = len(faux.demandes("subscribe"))
        ok("les symboles sont redemandés au battement",
           attendre_que(lambda: len(faux.demandes("subscribe")) >= n + 2, 3)
           and faux.demandes("subscribe")[-1] == ["AMD", "EURUSD=X", "SPY"])
        suivre("tableau", ["NVDA", "AMD"])
        ok("un second client : l'union des symboles",
           attendre_que(lambda: faux.demandes("subscribe")[-1] == ["AMD", "EURUSD=X", "NVDA", "SPY"]))
        suivre("position", ["AMD"])
        ok("un symbole qui n'est plus voulu est désabonné",
           attendre_que(lambda: ["EURUSD=X", "SPY"] in faux.demandes("unsubscribe")))
        ok("… et l'abonnement suit", faux.demandes("subscribe")[-1] == ["AMD", "NVDA"])
        faux.envoyer(message("SPY", 700.0, time.time() + FUTUR_S - 1))
        ok("un message d'un symbole désabonné est ignoré",
           attendre_que(lambda: etat()["ignores"].get("symbole", 0) >= 2) and cours("SPY").prix == 712.75)

        print("Plusieurs fils qui lisent pendant que le flux écrit")
        erreurs = []

        def lire_beaucoup():
            try:
                for _ in range(3000):
                    c = cours("AMD")
                    assert c is None or (isinstance(c, CoursDirect) and c.prix > 0)
                    etat()
            except Exception as e:    # noqa
                erreurs.append(e)
        lecteurs = [threading.Thread(target=lire_beaucoup) for _ in range(4)]
        for l in lecteurs:
            l.start()
        for k in range(40):
            faux.envoyer(message("AMD", 413.0 + k / 100, time.time()))
        for l in lecteurs:
            l.join()
        ok("aucune erreur en lisant pendant l'écriture", not erreurs)

        print("Les pannes")
        n0 = faux.connexions
        t_coupe = time.time()
        faux.envoyer("fermer")
        refaite = attendre_que(lambda: faux.connexions == n0 + 1 and etat()["etat"] == "ouvert", 3, AMD)
        duree = time.time() - t_coupe
        ok(f"une connexion fermée par Yahoo est refaite vite ({duree:.2f} s, avant ATTENTE_S : elle avait donné des cours)",
           refaite and duree < ATTENTE_S * 0.8)
        MUET_S = 0.6
        n0 = faux.connexions
        fin_parle = time.time() + 2.0
        while time.time() < fin_parle:
            faux.envoyer(message("AMD", 418.0, time.time()))
            suivre(*AMD)
            time.sleep(0.15)
        ok("un flux qui parle n'est jamais refait (2 s de messages, silence permis 0,6 s)",
           faux.connexions == n0 and etat()["etat"] == "ouvert")
        n0 = faux.connexions
        ok("une connexion muette : l'état dit pourquoi pendant l'attente",
           attendre_que(lambda: "resté muet" in (etat()["raison"] or ""), 4, AMD))
        ok("… puis elle est refaite", attendre_que(lambda: faux.connexions >= n0 + 1, 4, AMD))
        MUET_S = 30.0
        attendre_que(lambda: etat()["etat"] == "ouvert", 3, AMD)
        faux.refuser = True
        faux.envoyer("fermer")
        ok("Yahoo refuse : l'état dit pourquoi, en français",
           attendre_que(lambda: etat()["etat"] == "attente"
                        and "Yahoo refuse la connexion (code 503)" in (etat()["raison"] or ""), 4, AMD))
        essais0 = etat()["connexions"]
        attendre_que(lambda: False, 2 * ATTENTE_S + 0.2, AMD)
        essais = etat()["connexions"] - essais0
        ok(f"… et il réessaie sans s'acharner : {essais} essai(s) en {2 * ATTENTE_S + 0.2:.1f} s "
           f"(un par ATTENTE_S au plus)", 1 <= essais <= 3)
        faux.refuser = False
        ok("puis il accepte : la connexion revient",
           attendre_que(lambda: etat()["etat"] == "ouvert", 4, AMD))
        faux.envoyer(message("AMD", 420.0, time.time()))
        ok("… et les cours avec elle", attendre_que(lambda: cours("AMD").prix == 420.0, 4, AMD))

        print("Un flux qui accepte la connexion sans jamais répondre")
        import socket
        muet = socket.socket()
        muet.bind(("127.0.0.1", 0))
        muet.listen(8)
        clients = []

        def accepter():
            try:
                for _ in range(8):
                    clients.append(muet.accept())
            except OSError:          # la prise fermée à la fin du cas
                pass
        threading.Thread(target=accepter, daemon=True).start()
        arreter()
        OUVERTURE_S = 0.6
        activer(f"ws://127.0.0.1:{muet.getsockname()[1]}")
        suivre(*AMD)
        ok("la première connexion après un démarrage : relu vite le temps qu'elle s'ouvre",
           etat()["etat"] == "connexion" and rythme_rapide(False) is True)
        ok("elle n'aboutit pas : l'état dit pourquoi, et la page ralentit",
           attendre_que(lambda: etat()["etat"] == "attente", 4, AMD) and rythme_rapide(False) is False)
        ok("le nouvel essai, encore « connexion », ne fait PAS relire vite",
           attendre_que(lambda: etat()["connexions"] >= 2 and etat()["etat"] == "connexion", 6, AMD)
           and rythme_rapide(False) is False)
        arreter()
        OUVERTURE_S = reglages[7]
        for c, _ in clients:
            c.close()
        muet.close()
        activer(url)
        suivre(*AMD)
        attendre_que(lambda: etat()["etat"] == "ouvert", 4, AMD)
        faux.envoyer(message("AMD", 420.0, time.time()))
        attendre_que(lambda: cours("AMD") is not None and cours("AMD").prix == 420.0, 4, AMD)

        print("Un message trop gros")
        n0 = faux.connexions
        faux.envoyer(json.dumps({"message": "A" * (TAILLE_MAX + 100)}))
        ok("un message de plus de 64 Ko fait refaire la connexion",
           attendre_que(lambda: faux.connexions >= n0 + 1 and etat()["etat"] == "ouvert", 4, AMD))
        ok("le dernier cours gardé reste", cours("AMD").prix == 420.0)

        print("Une erreur imprévue dans le fil")
        vrai_garder = globals()["_garder"]

        def garder_qui_casse(*a):
            globals()["_garder"] = vrai_garder
            raise RuntimeError("panne imprévue")
        globals()["_garder"] = garder_qui_casse
        n0 = faux.connexions
        faux.envoyer(message("AMD", 421.0, time.time()))
        ok("elle ne tue pas le fil : il se reconnecte",
           attendre_que(lambda: faux.connexions == n0 + 1 and etat()["etat"] == "ouvert" and _fil["vivant"],
                        4, AMD))
        faux.envoyer(message("AMD", 422.0, time.time()))
        ok("… et garde de nouveau les cours", attendre_que(lambda: cours("AMD").prix == 422.0, 4, AMD))

        print("Plus de demande, la Bourse fermée")
        DEMANDE_S = 0.5
        ok("sans demande pendant DEMANDE_S, la connexion se ferme et le fil s'arrête",
           attendre_que(lambda: etat()["etat"] == "arrete" and not _fil["vivant"], 4))
        n0 = faux.connexions
        suivre("position", ["AMD"])
        ok("une nouvelle demande la rouvre", attendre_que(lambda: faux.connexions == n0 + 1
                                                          and etat()["etat"] == "ouvert"))
        bourse("fermee")
        ok("la Bourse fermée : la connexion se ferme, même si la page demande toujours",
           attendre_que(lambda: etat()["etat"] == "arrete" and not _fil["vivant"], 4, AMD))
        n0, depuis0 = faux.connexions, _etat["depuis"]
        attendre_que(lambda: False, 0.4, AMD)
        ok("… et les demandes ne la rouvrent pas (aucun fil lancé, l'état ne bouge pas)",
           faux.connexions == n0 and not _fil["vivant"] and _etat["depuis"] == depuis0
           and etat()["etat"] == "arrete")
        bourse("pas un état")
        ok("un état de la Bourse inconnu compte comme « on ne sait pas »", _bourse["etat"] is None)
        bourse("apres")
        suivre("position", ["AMD"])
        ok("la Bourse rouverte (après-séance) : la connexion revient",
           attendre_que(lambda: etat()["etat"] == "ouvert"))

        print("La veille du Mac (l'heure réelle saute)")
        decalage = [0.0]
        _maintenant = lambda: time.time() + decalage[0]     # noqa: E731
        decalage[0] = 7200.0
        ok("deux heures sautées : plus personne ne demande depuis 90 s, le fil s'arrête",
           attendre_que(lambda: etat()["etat"] == "arrete" and not _fil["vivant"], 4))
        ok("le dernier cours reste lisible, avec sa vraie heure (vieille de 2 h)",
           cours("AMD") is not None and _maintenant() - cours("AMD").heure.timestamp() > 7000)
        n0 = faux.connexions
        suivre("position", ["AMD"])
        ok("au retour, une demande rouvre une connexion neuve",
           attendre_que(lambda: faux.connexions == n0 + 1 and etat()["etat"] == "ouvert"))
        _maintenant = reglages[6]

        print("Une bibliothèque absente")
        arreter()
        activer(url)
        sauve = sys.modules.get("yfinance.pricing_pb2")
        sys.modules["yfinance.pricing_pb2"] = None
        try:
            n0 = faux.connexions
            suivre("position", ["AMD"])
            ok("le flux dit qu'il est indisponible, et pourquoi",
               attendre_que(lambda: etat()["etat"] == "indisponible" and "bibliothèque" in (etat()["raison"] or "")))
            ok("… sans ouvrir de connexion", faux.connexions == n0 and not _fil["vivant"])
            depuis0 = _etat["depuis"]
            attendre_que(lambda: False, 0.3, AMD)
            ok("… et il ne réessaie pas à chaque demande (aucun fil lancé, l'état ne bouge pas)",
               faux.connexions == n0 and not _fil["vivant"] and _etat["depuis"] == depuis0
               and etat()["etat"] == "indisponible")
        finally:
            sys.modules["yfinance.pricing_pb2"] = sauve
        arreter()
        ok("arreter() remet tout à zéro", etat()["etat"] == "arrete" and cours("AMD") is None
           and etat()["messages"] == 0)
    finally:
        DEMANDE_S, BATTEMENT_S, MUET_S, ATTENTE_S, REPRISE_S, RECV_S, _maintenant, OUVERTURE_S = reglages
        arreter()
        _actif["url"] = None
        faux.fermer()
        os.dup2(sortie_avant, 2)
        os.close(sortie_avant)
        capte.seek(0)
        ecrit = capte.read().decode("utf-8", "replace")
        capte.close()
    ok("rien n'a été écrit sur la sortie d'erreur", ecrit == "")
    if ecrit:
        print("    écrit :", ecrit[:500])
    bons = sum(1 for _, b in resultats if b)
    print(f"\n{bons}/{len(resultats)} cas justes")
    return bons == len(resultats)


if __name__ == "__main__":
    if "--verifier" in sys.argv:
        sys.exit(0 if verifier() else 1)
    if "--sonder" in sys.argv:
        args = [a for a in sys.argv[1:] if a != "--sonder"]
        duree = int(args[0]) if args and args[0].isdigit() else 180
        noms = [a.upper() for a in args[1:]] if args and args[0].isdigit() else [a.upper() for a in args]
        sonder(duree, tuple(noms) or ("SPY", "AMD", "TSLA"))
        sys.exit(0)
    print(__doc__)
