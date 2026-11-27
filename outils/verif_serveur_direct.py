"""
Les morceaux du serveur pour le direct (web/server.py, plan du 24/11/2026),
vérifiés sans réseau : references_du_scan, valeurs_en_direct (le tableau des
49 en direct), mon_journal (il rend sa position), duree_cache_position,
ma_position_en_direct (l'état de la Bourse donné au flux AVANT le calcul),
etat_de_la_bourse (le forçage d'essai, la mémoire des séances) et
sortie_d_erreur_dans_un_fichier. Yahoo, le flux et le calcul de la position
sont remplacés par des faux ; rien n'est écrit dans data/.

    /usr/bin/python3 outils/verif_serveur_direct.py
"""

import importlib.util
import math
import os
import subprocess
import sys
import tempfile
import time
import warnings
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

warnings.filterwarnings("ignore")
PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJET)
FICHIER_SERVEUR = os.environ.get("SERVEUR_A_VERIFIER") or os.path.join(PROJET, "web", "server.py")
_spec = importlib.util.spec_from_file_location("serveur_verifie", FICHIER_SERVEUR)
serveur = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(serveur)

import pandas as pd          # noqa: E402
import cours_direct          # noqa: E402
import papier                # noqa: E402

cas = []


def ok(nom, condition):
    cas.append(bool(condition))
    print(("✓ " if condition else "✗ ") + nom, flush=True)


def proche(a, b, tol=1e-12):
    return a is not None and b is not None and abs(a - b) <= tol


def references():
    print("Les repères du scan")
    jours = pd.date_range("2025-08-01", periods=320, freq="B")
    cl = [100.0 + i * 0.37 + (i % 7) * 1.1 for i in range(320)]
    df = pd.DataFrame({"Open": cl, "High": [c + 1 for c in cl], "Low": [c - 1 for c in cl], "Close": cl,
                       "Volume": [1_000_000 + 1000 * (i % 5) for i in range(320)]}, index=jours)
    r = serveur.references_du_scan(df)
    ok("la date de la dernière séance du scan", r["date_seance"] == jours[-1].date().isoformat())
    ok("les clôtures de la veille, d'il y a 5 et 22 séances (écrites à la main)",
       r["references"] == {"change_1d": cl[-2], "change_1w": cl[-6], "change_1m": cl[-23]})
    # Les MÊMES repères que stock_bot.analyze : ses variations se retrouvent au bit près.
    import stock_bot
    bot = stock_bot.StockSignalBot(tickers=["AMD"], capital=100, dashboard=False)
    a = bot.analyze("AMD", df)
    retrouve = all((a["price"] - r["references"][cle]) / r["references"][cle] * 100 == a[cle]
                   for cle in ("change_1d", "change_1w", "change_1m"))
    ok("les variations de stock_bot.analyze se retrouvent au bit près avec ces repères", retrouve)
    court = serveur.references_du_scan(df.iloc[-10:])
    ok("un historique de 10 séances : pas de repère à 22 séances (None), comme le scan (0)",
       court["references"]["change_1m"] is None and court["references"]["change_1w"] == cl[-6])
    abime = df.copy()
    abime.iloc[-2, abime.columns.get_loc("Close")] = 0.0
    abime.iloc[-6, abime.columns.get_loc("Close")] = float("nan")
    rr = serveur.references_du_scan(abime)["references"]
    ok("un repère nul ou non fini devient None (jamais une division par zéro)",
       rr["change_1d"] is None and rr["change_1w"] is None and rr["change_1m"] == cl[-23])


def tableau():
    print("Le tableau des 49 en direct")
    maintenant = datetime.now(timezone.utc)
    aujourd_hui = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    refs = {"change_1d": 400.0, "change_1w": 380.0, "change_1m": 500.0}
    # Le scan a 2 minutes (son heure, comme scan_stocks : datetime.now() sans fuseau).
    scan_a = (datetime.now() - timedelta(minutes=2)).isoformat()
    scan = {"rows": [
        {"ticker": "AMD", "date_seance": aujourd_hui, "references": refs, "price": 405.0},
        {"ticker": "MSFT", "date_seance": aujourd_hui, "references": {"change_1d": 300.0, "change_1w": None,
                                                                      "change_1m": 250.0}},
        {"ticker": "NVDA", "date_seance": "2026-01-02", "references": refs},
        {"ticker": "TSLA", "date_seance": aujourd_hui, "references": refs},
        {"ticker": "KO", "date_seance": aujourd_hui, "references": refs},
        {"ticker": "PEP", "date_seance": aujourd_hui, "references": refs},
        {"ticker": "WMT", "date_seance": aujourd_hui, "references": refs},
        {"ticker": "XOM", "error": "données indisponibles"},
    ], "scanned_at": scan_a}
    CD = cours_direct.CoursDirect
    il_y_a = lambda s: maintenant - timedelta(seconds=s)          # noqa: E731
    # Le dernier message (toutes séances) et le dernier de la séance normale.
    dernier = {"TSLA": CD(380.0, il_y_a(2), 2)}
    normale = {"AMD": CD(420.0, il_y_a(3), 1),
               "MSFT": CD(330.0, il_y_a(cours_direct.FRAIS_S - 1), 1),
               "NVDA": CD(150.0, il_y_a(2), 1),
               "KO": CD(70.0, il_y_a(cours_direct.FRAIS_S + 1), 1),     # plus frais, mais plus récent que le scan
               "WMT": CD(90.0, il_y_a(300), 1)}                         # plus ancien que le scan
    appels = []
    reel = (cours_direct.cours, cours_direct.cours_normal, cours_direct.suivre, cours_direct.bourse,
            cours_direct.etat, serveur.etat_de_la_bourse)
    cours_direct.cours = lambda s: dernier.get(s) or normale.get(s)
    cours_direct.cours_normal = lambda s: normale.get(s)
    cours_direct.suivre = lambda client, symboles: appels.append(("suivre", client, list(symboles)))
    cours_direct.bourse = lambda e: appels.append(("bourse", e))
    cours_direct.etat = lambda: {"etat": "ouvert", "raison": None, "depuis_s": 100.0, "actif": True}
    serveur.etat_de_la_bourse = lambda: "ouverte"
    serveur._cache.clear()
    try:
        vide = serveur.valeurs_en_direct()
        ok("sans scan en mémoire : aucune valeur, pas « en direct », rien ne casse",
           vide["valeurs"] == {} and vide["en_direct"] is False and vide["scan_de"] is None)
        serveur._cache["/api/stocks"] = {"value": scan, "at": time.time()}
        appels.clear()
        v = serveur.valeurs_en_direct()
        amd = v["valeurs"].get("AMD", {})
        ok("AMD en séance, cours de 3 s : 420 $, +5 % sur un jour (420 / 400), écrits à la main",
           amd.get("price") == 420.0 and proche(amd.get("change_1d"), 5.0)
           and proche(amd.get("change_1w"), 40 / 380 * 100) and proche(amd.get("change_1m"), -16.0))
        ok("… frais, avec l'heure du cours", amd.get("frais") is True and amd.get("heure") == normale["AMD"].heure.isoformat())
        msft = v["valeurs"].get("MSFT", {})
        ok("MSFT à 29 s : frais ; un repère absent donne une variation absente (None)",
           msft.get("frais") is True and msft.get("change_1w") is None and proche(msft.get("change_1d"), 10.0))
        ok("NVDA : le scan n'a pas encore la séance du jour, ses chiffres restent ceux du scan", "NVDA" not in v["valeurs"])
        ok("TSLA : seulement des cours d'après-séance, rien dans le tableau", "TSLA" not in v["valeurs"])
        ko = v["valeurs"].get("KO", {})
        ok("KO à 31 s, plus récent que le scan : gardé (la case ne recule pas), mais pas frais",
           ko.get("price") == 70.0 and ko.get("frais") is False)
        ok("WMT, plus ancien que le scan : les chiffres du scan", "WMT" not in v["valeurs"])
        ok("PEP (pas de cours de séance) et XOM (en erreur) n'y sont pas", "PEP" not in v["valeurs"] and "XOM" not in v["valeurs"])
        ok("« en direct » et relu vite (un cours frais au moins)", v["en_direct"] is True and v["rapide"] is True)
        ok("le flux suit les valeurs du scan (pas celle en erreur), et apprend l'état de la Bourse d'abord",
           appels == [("bourse", "ouverte"), ("suivre", "tableau", ["AMD", "MSFT", "NVDA", "TSLA", "KO", "PEP", "WMT"])])
        ok("l'heure du scan est donnée", v["scan_de"] == scan_a)
        for s in ("AMD", "MSFT"):
            del normale[s]
        v = serveur.valeurs_en_direct()
        ok("plus aucun cours frais : KO reste (plus récent que le scan), mais pas « en direct »",
           set(v["valeurs"]) == {"KO"} and v["en_direct"] is False and v["rapide"] is False)
        scan["scanned_at"] = datetime.now().isoformat()
        ok("un scan plus récent que le cours de KO : ses chiffres reprennent la case", "KO" not in serveur.valeurs_en_direct()["valeurs"])
        scan["scanned_at"] = "pas une heure"
        normale["AMD"] = CD(420.0, il_y_a(3), 1)
        v = serveur.valeurs_en_direct()
        ok("une heure de scan illisible : seuls les cours frais (pas de comparaison possible)", set(v["valeurs"]) == {"AMD"})
        scan["scanned_at"] = scan_a
        del serveur._cache["/api/stocks"]
        serveur._cache["/api/stocks:classement"] = {"value": scan, "at": time.time()}
        ok("le classement gardé après une décision notée sert aussi", "AMD" in serveur.valeurs_en_direct()["valeurs"])
        for etat_bourse, demande in (("apres", False), ("avant", False), ("fermee", False), (None, True)):
            serveur.etat_de_la_bourse = lambda e=etat_bourse: e
            appels.clear()
            serveur.valeurs_en_direct()
            demandes = [a for a in appels if a[0] == "suivre"]
            ok(f"Bourse « {etat_bourse} » : les 49 {'sont' if demande else 'ne sont pas'} demandés au flux",
               bool(demandes) == demande and ("bourse", etat_bourse) in appels)
        serveur.etat_de_la_bourse = lambda: "ouverte"
        normale.clear()
        cours_direct.etat = lambda: {"etat": "attente", "raison": "Yahoo refuse la connexion (code 503)",
                                     "depuis_s": 3.0, "actif": True}
        v = serveur.valeurs_en_direct()
        ok("une panne du flux : aucune valeur, lent, et la raison est donnée",
           v["valeurs"] == {} and v["en_direct"] is False and v["rapide"] is False and "503" in v["raison"])
    finally:
        (cours_direct.cours, cours_direct.cours_normal, cours_direct.suivre, cours_direct.bourse,
         cours_direct.etat, serveur.etat_de_la_bourse) = reel
        serveur._cache.clear()


def journal_et_cache():
    print("Le journal, sa position, et le cache")
    import ma_position
    calculs = []
    ordre = []

    def fausse_position():
        calculs.append(time.time())
        return {"lignes": [{"ticker": "AMD", "prix_usd": 420.0 + len(calculs)}], "eurusd": 1.10, "n": len(calculs)}
    reel = (serveur.ma_position_en_direct, ma_position.journal_compare, cours_direct.etat)
    serveur.ma_position_en_direct = fausse_position
    ma_position.journal_compare = lambda prix, fx: {"decisions": [], "prix_vus": dict(prix), "fx_vu": fx}
    serveur._cache.clear()
    try:
        for e, attendu in (("connexion", 1), ("ouvert", 1), ("attente", 60), ("arrete", 60), ("indisponible", 60)):
            cours_direct.etat = lambda e=e: {"etat": e}
            ok(f"la position gardée {attendu} s quand le flux est « {e} »", serveur.duree_cache_position() == attendu)
        cours_direct.etat = lambda: {"etat": "ouvert", "depuis_s": 100.0}
        j = serveur.mon_journal()
        ok("le journal rend la position qu'il a utilisée", j.get("position", {}).get("n") == 1)
        ok("… et compte avec ses cours (une seule photo)", j["prix_vus"] == {"AMD": 421.0} and j["fx_vu"] == 1.10)
        p = serveur.position_servie()
        ok("/api/position, demandée dans la seconde, rend le MÊME calcul", p["n"] == 1 and p["lignes"] == j["position"]["lignes"])
        serveur._cache["/api/position:calcul"]["at"] -= 1.5
        j2 = serveur.mon_journal()
        ok("pendant le direct, une position de plus d'une seconde est recalculée", j2["position"]["n"] == 2)
        cours_direct.etat = lambda: {"etat": "arrete"}
        serveur._cache["/api/position:calcul"]["at"] -= 30
        ok("sans le direct, elle reste gardée 60 s (comme avant)", serveur.mon_journal()["position"]["n"] == 2)
        # L'état du direct est recalculé à chaque lecture, jamais servi depuis le cache.
        cours_direct.etat = lambda: {"etat": "connexion", "premier_essai": True, "depuis_s": 1.0}
        serveur._cache.clear()
        ok("pendant la première connexion : rapide", serveur.position_servie()["direct"]["rapide"] is True)
        cours_direct.etat = lambda: {"etat": "attente", "premier_essai": False, "depuis_s": 1.0,
                                     "raison": "Yahoo ne répond pas (délai dépassé)"}
        p = serveur.position_servie()
        ok("la connexion ratée : le calcul en cache sert encore, mais l'état du direct est relu (lent, la raison)",
           p["n"] == 3 and p["direct"]["rapide"] is False and "délai" in p["direct"]["raison"])
        ok("… et le journal dit la même chose", serveur.mon_journal()["position"]["direct"] == p["direct"])
    finally:
        serveur.ma_position_en_direct, ma_position.journal_compare, cours_direct.etat = reel
        serveur._cache.clear()

    print("L'état de la Bourse est donné au flux AVANT le calcul")
    reel = (serveur.etat_de_la_bourse, cours_direct.bourse, ma_position.position)
    serveur.etat_de_la_bourse = lambda: "fermee"
    cours_direct.bourse = lambda e: ordre.append(("bourse", e))
    ma_position.position = lambda rang_de=None, seances=None: ordre.append(("position",)) or {"lignes": []}
    try:
        p = serveur.ma_position_en_direct()
        ok("l'ordre : la Bourse, puis la position ; et la position la dit",
           ordre == [("bourse", "fermee"), ("position",)] and p["bourse"] == "fermee")
    finally:
        serveur.etat_de_la_bourse, cours_direct.bourse, ma_position.position = reel


def bourse():
    print("L'état de la Bourse")
    maintenant = datetime.now(timezone.utc)
    ouverte = {"jour": None, "courante": (maintenant - timedelta(hours=1), maintenant + timedelta(hours=5)),
               "avant": (maintenant - timedelta(hours=6), maintenant - timedelta(hours=1)),
               "apres": (maintenant + timedelta(hours=5), maintenant + timedelta(hours=9))}
    passee = {"jour": None, "courante": (maintenant - timedelta(hours=20), maintenant - timedelta(hours=14)),
              "avant": (maintenant - timedelta(hours=25), maintenant - timedelta(hours=20)),
              "apres": (maintenant - timedelta(hours=14), maintenant - timedelta(hours=10))}
    lectures = []
    reponse = {"periode": ouverte, "panne": False}

    def fausse_minute(symbole, relire=False):
        lectures.append(symbole)
        if reponse["panne"]:
            raise ValueError("Yahoo ne répond pas")
        return 700.0, maintenant, reponse["periode"]
    reel = (papier.minute_de_seance, dict(serveur._seances_bourse), os.environ.get("FLUX_URL"),
            os.environ.get("FLUX_BOURSE"))
    papier.minute_de_seance = fausse_minute
    for cle in ("FLUX_URL", "FLUX_BOURSE"):
        os.environ.pop(cle, None)
    serveur._seances_bourse.update(lu=0.0, periode=None, essai=0.0)
    erreur_avant = sys.stderr
    sys.stderr = open(os.devnull, "w")       # la ligne « état de la Bourse » de la panne, voulue ici
    try:
        ok("ouverte, d'après les séances de Yahoo", serveur.etat_de_la_bourse() == "ouverte" and lectures == ["SPY"])
        ok("… gardées : une seconde demande ne relit pas Yahoo", serveur.etat_de_la_bourse() == "ouverte"
           and lectures == ["SPY"])
        serveur._seances_bourse["essai"] -= 61
        serveur.etat_de_la_bourse()
        ok("… ni une minute plus tard (gardées une heure)", lectures == ["SPY"])
        serveur._seances_bourse["lu"] -= 3601
        serveur._seances_bourse["essai"] -= 3601
        serveur.etat_de_la_bourse()
        ok("relues après une heure", lectures == ["SPY", "SPY"])
        reponse["periode"] = passee
        serveur._seances_bourse.update(lu=time.time() - 3601, essai=0.0)
        ok("fermée (la séance du jour est passée)", serveur.etat_de_la_bourse() == "fermee")
        n = len(lectures)
        serveur.etat_de_la_bourse()
        ok("fermée : pas relue avant 5 minutes", len(lectures) == n)
        serveur._seances_bourse["lu"] -= 301
        serveur._seances_bourse["essai"] -= 301
        reponse["periode"] = ouverte
        ok("fermée depuis 5 minutes : relue (et la voilà ouverte)", serveur.etat_de_la_bourse() == "ouverte"
           and len(lectures) == n + 1)
        reponse["panne"] = True
        serveur._seances_bourse.update(lu=time.time() - 3601, essai=0.0)
        n = len(lectures)
        e1 = serveur.etat_de_la_bourse()
        serveur.etat_de_la_bourse()
        ok("Yahoo en panne : l'ancien état reste, et un seul essai par minute",
           e1 == "ouverte" and len(lectures) == n + 1)
        serveur._seances_bourse.update(lu=0.0, periode=None, essai=0.0)
        ok("Yahoo n'a jamais répondu : None (la page fait son propre calcul)", serveur.etat_de_la_bourse() is None)
        os.environ["FLUX_BOURSE"] = "fermee"
        serveur._seances_bourse.update(lu=0.0, periode=None, essai=0.0)
        reponse["panne"] = False
        ok("FLUX_BOURSE sans faux flux (FLUX_URL) est ignoré", serveur.etat_de_la_bourse() == "ouverte")
        os.environ["FLUX_URL"] = "ws://127.0.0.1:9"
        ok("avec un faux flux, FLUX_BOURSE impose l'état (les vérifications de la page)",
           serveur.etat_de_la_bourse() == "fermee")
        os.environ["FLUX_BOURSE"] = "n'importe quoi"
        ok("… un état inconnu donne None", serveur.etat_de_la_bourse() is None)
    finally:
        sys.stderr.close()
        sys.stderr = erreur_avant
        papier.minute_de_seance = reel[0]
        serveur._seances_bourse.clear()
        serveur._seances_bourse.update(reel[1])
        for cle, v in (("FLUX_URL", reel[2]), ("FLUX_BOURSE", reel[3])):
            if v is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = v


def seances():
    print("Les séances de Yahoo dans la position et le graphique (séance courte, 27/11/2026)")
    import ma_position
    u = lambda j, h, mi: datetime(2026, 11, j, h, mi, tzinfo=timezone.utc)
    courte = {"jour": (u(27, 14, 30), u(27, 18, 0)), "courante": (u(27, 14, 30), u(27, 18, 0)),
              "avant": None, "apres": None}
    vus = {"position": [], "graphique": [], "retenues": []}
    reel = (ma_position.position, ma_position.graphique, ma_position.retenir_seances, serveur.etat_de_la_bourse,
            dict(serveur._seances_bourse), papier.minute_de_seance, os.environ.get("FLUX_URL"),
            os.environ.get("FLUX_BOURSE"))

    def fausse_position(rang_de=None, seances=None):
        vus["position"].append(seances)
        return {"lignes": [], "total": {}, "direct": {}}

    def faux_graphique(ticker, periode, seances=None):
        vus["graphique"].append(seances)
        return {"ticker": ticker, "periode": periode}
    ma_position.position, ma_position.graphique = fausse_position, faux_graphique
    for cle in ("FLUX_URL", "FLUX_BOURSE"):
        os.environ.pop(cle, None)
    erreur_avant = sys.stderr
    sys.stderr = open(os.devnull, "w")       # la ligne « séances courtes : disque plein », voulue ici
    try:
        appels = []
        serveur.etat_de_la_bourse = lambda: appels.append(1) or "ouverte"
        serveur._seances_bourse.update(periode=courte)
        serveur.ma_position_en_direct()
        ok("la position reçoit les séances que le serveur garde", vus["position"] == [courte])
        with serveur._cache_lock:
            serveur._cache.clear()
        n = len(appels)
        serveur.graphique_servi("SPY", "1a")
        serveur.graphique_servi("SPY", "1a")
        ok("le graphique les reçoit aussi, gardé en cache (un calcul pour deux demandes), sans attendre Yahoo",
           vus["graphique"] == [courte] and len(appels) == n)
        serveur._seances_bourse.update(periode=None)
        with serveur._cache_lock:
            serveur._cache.clear()
        vus["graphique"].clear()
        n = len(appels)
        serveur.graphique_servi("SPY", "1a")
        ok("séances pas encore lues : une lecture tentée, calculé sans elles, pas rangé sous la clé normale (1 h)",
           vus["graphique"] == [None] and len(appels) == n + 1 and "/api/graphique?SPY&1a" not in serveur._cache)
        serveur._seances_bourse.update(periode=courte)
        serveur.graphique_servi("SPY", "1a")
        ok("… dès qu'elles sont lues, le graphique les a (pas le calcul fait sans elles)",
           vus["graphique"] == [None, courte])
        serveur.etat_de_la_bourse = reel[3]
        ma_position.retenir_seances = lambda p: vus["retenues"].append(p)
        papier.minute_de_seance = lambda symbole, relire=False: (700.0, u(27, 15, 0), courte)
        serveur._seances_bourse.update(lu=0.0, periode=None, essai=0.0)
        serveur.etat_de_la_bourse()
        ok("chaque lecture des séances de Yahoo est donnée à ma_position.retenir_seances", vus["retenues"] == [courte])

        def disque_plein(_):
            raise OSError("disque plein")
        ma_position.retenir_seances = disque_plein
        serveur._seances_bourse.update(lu=0.0, periode=None, essai=0.0)
        ok("… et un disque plein ne casse pas l'état de la Bourse (séances gardées en mémoire)",
           serveur.etat_de_la_bourse() in ("avant", "ouverte", "apres", "fermee")
           and serveur._seances_bourse["periode"] is courte)
    finally:
        sys.stderr.close()
        sys.stderr = erreur_avant
        (ma_position.position, ma_position.graphique, ma_position.retenir_seances, serveur.etat_de_la_bourse) = reel[:4]
        serveur._seances_bourse.clear()
        serveur._seances_bourse.update(reel[4])
        papier.minute_de_seance = reel[5]
        with serveur._cache_lock:
            serveur._cache.clear()
        for cle, v in (("FLUX_URL", reel[6]), ("FLUX_BOURSE", reel[7])):
            if v is None:
                os.environ.pop(cle, None)
            else:
                os.environ[cle] = v


def thermometre_panne():
    print("Le thermomètre pendant une panne de Yahoo (plan de la v finale, 27/11/2026)")
    import thermometre
    reel = (thermometre.thermometre, dict(serveur._thermo_recent))
    reponse = {"panne": False, "n": 0, "pendant": None, "valeur": None}
    bonne = {"lecture": {"date": "2026-11-27", "niveau": 1}, "mesure": None, "avertissement": "…"}
    nouvelle = {"lecture": {"date": "2026-11-30", "niveau": 0}, "mesure": None, "avertissement": "…"}

    def faux():
        reponse["n"] += 1
        if reponse["pendant"]:
            reponse["pendant"]()                 # une décision notée pendant ce calcul
        if reponse["panne"]:
            raise ValueError("aucun cours reçu pour ^GSPC (Yahoo refuse)")
        return reponse["valeur"] or bonne

    def route():
        """Comme le serveur : la durée de cache lue à chaque demande, puis cached()."""
        producteur, duree = serveur.ROUTES["/api/thermometre"]
        return serveur.cached("/api/thermometre", duree() if callable(duree) else duree, producteur)[0]

    def vieillir(secondes):
        with serveur._cache_lock:
            entree = serveur._cache.get("/api/thermometre")
            if entree:
                entree["at"] -= secondes
        serveur._thermo_recent["quand"] -= secondes

    def noter_une_decision():
        serveur._cache_generation[0] += 1
    thermometre.thermometre = faux
    try:
        with serveur._cache_lock:
            serveur._cache.pop("/api/thermometre", None)
        serveur._thermo_recent.update(valeur=None, quand=0.0)
        reponse["panne"] = True
        try:
            route()
            erreur = False
        except ValueError:
            erreur = True
        ok("Yahoo en panne dès le lancement : l'erreur reste (la page dit « indisponible »)", erreur)
        reponse["panne"] = False
        v = route()
        ok("une lecture réussie : rendue telle quelle, gardée une heure",
           v is bonne and serveur.duree_cache_thermometre() == 3600)
        n = reponse["n"]
        vieillir(1800)
        route()
        ok("… une demande 30 min plus tard reprend la même, sans Yahoo", reponse["n"] == n)
        vieillir(1801)
        reponse["panne"] = True
        v = route()
        ok("Yahoo en panne après une heure : la dernière lecture, avec son heure et la raison (sans toucher à la lecture gardée)",
           v["lecture"] == bonne["lecture"] and v["secours"]["raison"].startswith("aucun cours reçu")
           and datetime.fromisoformat(v["secours"]["lue_le"]) < datetime.now(timezone.utc) - timedelta(minutes=59)
           and "secours" not in bonne)
        ok("… gardée 5 minutes seulement : Yahoo sera relu bientôt", serveur.duree_cache_thermometre() == 300)
        n = reponse["n"]
        vieillir(240)
        route()
        ok("… à 4 minutes, toujours la lecture de secours, sans Yahoo", reponse["n"] == n)
        reponse.update(panne=False, valeur=nouvelle, pendant=noter_une_decision)
        vieillir(61)
        v = route()
        ok("Yahoo revenu pendant qu'une décision est notée : la nouvelle lecture est servie",
           v is nouvelle and reponse["n"] == n + 1)
        ok("… pas rangée (décision notée), et la lecture de secours rangée reste à 5 minutes, pas une heure",
           serveur.duree_cache_thermometre() == 300)
        reponse["pendant"] = None
        vieillir(301)
        v = route()
        ok("… 5 minutes plus tard, la nouvelle lecture est rangée et gardée une heure",
           v is nouvelle and "secours" not in v and serveur.duree_cache_thermometre() == 3600)
        ok("la route du thermomètre lit sa durée de cache à chaque demande",
           serveur.ROUTES["/api/thermometre"][1] is serveur.duree_cache_thermometre)
    finally:
        thermometre.thermometre = reel[0]
        serveur._thermo_recent.clear()
        serveur._thermo_recent.update(reel[1])
        with serveur._cache_lock:
            serveur._cache.pop("/api/thermometre", None)


def statut_du_bot():
    print("Le statut du bot (un fichier PID périmé repris par un autre programme, 27/11/2026)")
    avant = os.getcwd()
    faux_bot = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "stock_bot.py"])
    with tempfile.TemporaryDirectory() as dossier:
        os.makedirs(os.path.join(dossier, "logs"))
        os.makedirs(os.path.join(dossier, "data"))
        os.chdir(dossier)                         # bots_status lit logs/ et data/ là où il tourne
        try:
            time.sleep(0.3)
            with open(os.path.join("logs", "stock_bot.pid"), "w") as f:
                f.write(str(os.getpid()))         # vivant, mais ce n'est pas un bot (ce script)
            perime = serveur.bots_status()["stock_bot"]["process"]
            with open(os.path.join("logs", "stock_bot.pid"), "w") as f:
                f.write(str(faux_bot.pid))
            vivant = serveur.bots_status()["stock_bot"]["process"]
            ok("un PID vivant qui n'est pas un stock_bot.py s'affiche arrêté ; un vrai bot, en marche",
               perime == {"pid": os.getpid(), "running": False} and vivant == {"pid": faux_bot.pid, "running": True})
        finally:
            os.chdir(avant)
            faux_bot.kill()
            faux_bot.wait()


def sortie_d_erreur():
    print("La sortie d'erreur quand l'appli lance le serveur")
    script = ("import importlib.util, os, sys\n"
              f"spec = importlib.util.spec_from_file_location('s', {FICHIER_SERVEUR!r})\n"
              "s = importlib.util.module_from_spec(spec); spec.loader.exec_module(s)\n"
              "print('avant', file=sys.stderr, flush=True)\n"
              "s.sortie_d_erreur_dans_un_fichier(dossier=sys.argv[1])\n"
              "print('après', file=sys.stderr, flush=True)\n"
              "os.write(2, b'brut\\n')\n")
    with tempfile.TemporaryDirectory() as dossier:
        env = {k: v for k, v in os.environ.items() if k != "DASHBOARD_PARENT_PID"}
        r = subprocess.run([sys.executable, "-c", script, dossier], capture_output=True, text=True,
                           env={**env, "DASHBOARD_PARENT_PID": "1"}, timeout=120)
        chemin = os.path.join(dossier, "serveur.log")
        fichier = open(chemin).read() if os.path.exists(chemin) else ""
        ok("lancé par l'appli : après la bascule, plus rien dans le tuyau",
           "avant" in r.stderr and "après" not in r.stderr and "brut" not in r.stderr)
        ok("… tout va dans logs/serveur.log (Python et le reste)", "après" in fichier and "brut" in fichier)
        with open(chemin, "w") as f:
            f.write("vieux\n" * 1000)
        subprocess.run([sys.executable, "-c", script, dossier], capture_output=True, text=True,
                       env={**env, "DASHBOARD_PARENT_PID": "1"}, timeout=120)
        ok("le fichier est vidé à chaque lancement", "vieux" not in open(chemin).read())
        r = subprocess.run([sys.executable, "-c", script, dossier + "-pas-utilise"], capture_output=True,
                           text=True, env=env, timeout=120)
        ok("lancé au Terminal (sans l'appli) : rien ne change, tout reste sur la sortie d'erreur",
           "après" in r.stderr and "brut" in r.stderr and not os.path.exists(dossier + "-pas-utilise"))


def main():
    import ma_position
    # Rien dans data/ : les séances courtes que le serveur garde vont à côté du journal, et un premier
    # essai du 27/11/2026 y avait écrit les séances inventées de bourse(). Le journal pointe donc vers
    # un dossier jetable pendant tout l'essai, et le vrai fichier est surveillé.
    vrai = os.path.join(PROJET, "data", "seances_courtes.json")
    photo = lambda: os.stat(vrai).st_mtime_ns if os.path.exists(vrai) else None
    avant, reel_journal = photo(), ma_position.JOURNAL
    with tempfile.TemporaryDirectory() as dossier:
        ma_position.JOURNAL = os.path.join(dossier, "journal.json")
        ma_position._seances_gardees["chemin"] = None
        try:
            references()
            tableau()
            journal_et_cache()
            bourse()
            seances()
            thermometre_panne()
            statut_du_bot()
            sortie_d_erreur()
        finally:
            ma_position.JOURNAL = reel_journal
            ma_position._seances_gardees["chemin"] = None
    ok("rien n'a été écrit dans data/ (data/seances_courtes.json inchangé)", photo() == avant)
    print(f"\n{sum(cas)}/{len(cas)} vérifications réussies")
    sys.exit(0 if all(cas) else 1)


if __name__ == "__main__":
    main()
