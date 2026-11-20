"""
Les erreurs volontaires du banc des prévisions (banc.py et banc_methodes.py).

CE QUE ÇA FAIT
    Chaque erreur est glissée dans une COPIE des deux fichiers, dans un
    dossier jetable (stock_engine.py y est lié, pas copié), puis
    `python3 banc.py --verifier` y est lancé : il doit échouer. Une erreur
    qui passe montre un trou dans les vérifications. Les fichiers du projet
    ne sont jamais modifiés, et rien n'est écrit dans data/.

LANCER
    python3 outils/mutations_banc.py           # toutes (environ 10 minutes)
    python3 outils/mutations_banc.py 3 17      # seulement les erreurs n° 3 et 17
"""

import concurrent.futures
import os
import shutil
import subprocess
import sys
import tempfile
import time

PROJET = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COPIES = ("banc.py", "banc_methodes.py")
LIENS = ("stock_engine.py",)
M, B = "banc_methodes.py", "banc.py"

# (fichier, [(avant, après), …], ce que l'erreur casse). « avant » doit être unique dans le fichier.
ERREURS = [
    # Les mesures : un regard vers le futur, ou un niveau à la place d'un rapport.
    (M, [('m["r1"] = c / c.shift(1) - 1', 'm["r1"] = c.shift(-1) / c - 1')], "r1 lit le lendemain"),
    (M, [('m["r5"] = c / c.shift(5) - 1', 'm["r5"] = c / c.shift(4) - 1')], "r5 sur 4 séances"),
    (M, [('m["r21"] = c / c.shift(21) - 1', 'm["r21"] = c.shift(-21) / c - 1')], "r21 lit le futur"),
    (M, [('m["vol20"] = ind["daily_vol"]', 'm["vol20"] = ind["daily_vol"].shift(-1)')], "volatilité du lendemain"),
    (M, [('m["rsi14"] = ind["rsi"]', 'm["rsi14"] = ind["rsi"].shift(-1)')], "RSI du lendemain"),
    (M, [('m["macd_atr"] = (ind["ema12"] - ind["ema26"]) / ind["atr"]', 'm["macd_atr"] = (ind["ema12"] - ind["ema26"])')],
     "MACD en niveau (trahit les fractionnements)"),
    (M, [('m["ecart20_atr"] = (c - ind["sma20"]) / ind["atr"]', 'm["ecart20_atr"] = (c - ind["sma20"])')],
     "écart à la moyenne en niveau"),
    (M, [('m["sma20_50"] = ind["sma20"] / ind["sma50"] - 1', 'm["sma20_50"] = ind["sma20"] - ind["sma50"]')],
     "moyennes en niveau"),
    (M, [('m["volume20"] = ind["volume"] / ind["vol_sma20"]', 'm["volume20"] = ind["volume"]')], "volume en niveau"),
    (M, [('m["mom12_1"] = ind["mom_12_1"]', 'm["mom12_1"] = ind["mom_12_1"].shift(-1)')], "momentum du lendemain"),
    (M, [('m["force"] = force.reindex(d.index)', 'm["force"] = force.reindex(d.index).shift(-1)')],
     "force du lendemain"),
    (M, [('m["spy1"] = (spy / spy.shift(1) - 1).reindex(d.index)', 'm["spy1"] = (spy.shift(-1) / spy - 1).reindex(d.index)')],
     "SPY du lendemain"),
    (M, [('m["spy5"] = (spy / spy.shift(5) - 1).reindex(d.index)', 'm["spy5"] = (spy / spy.shift(-5) - 1).reindex(d.index)')],
     "SPY sur 5 séances futures"),
    (M, [('m["eurusd1"] = connu_le_soir(fx1, d.index)', 'm["eurusd1"] = connu_le_soir(fx1.shift(-1), d.index)')],
     "l'euro/dollar de la ligne suivante"),
    (M, [('return fx1.reindex(dates, method="ffill", tolerance', 'return fx1.reindex(dates, method="bfill", tolerance')],
     "l'euro/dollar d'une ligne d'après le soir"),
    (M, [('method="ffill", tolerance=pd.Timedelta(days=banc.ECART_CHANGE_MAX))', 'method="ffill")')],
     "un euro/dollar périmé de plus de 5 jours"),
    (M, [('    return change / change.shift(1) - 1', '    return change.shift(-1) / change - 1')],
     "euro/dollar vers l'ouverture suivante"),
    (M, [('fx1 = change_sur_un_jour(change)\n', 'fx1 = change_sur_un_jour(change).shift(-1)\n')],
     "la variation du change décalée d'une ligne"),
    (M, [('r = banc.rendements_du_lendemain(banc.en_euros(clotures, change))[tickers]',
          'r = banc.rendements_du_lendemain(clotures)[tickers]')], "la réponse en dollars, pas en euros"),
    (M, [('X[:, j, :] = mesures_d_une_valeur(cours[t], spy, fx1)', 'X[:, j, :] = mesures_d_une_valeur(cours[tickers[0]], spy, fx1)')],
     "les mesures de la première valeur pour toutes"),
    (M, [('m["r1"] = c / c.shift(1) - 1', 'm["r1"] = c / c.shift(2) - 1')], "« comme hier » sur deux séances"),
    # L'apprentissage.
    (M, [('return np.arange(max(premiere - purge, 0))', 'return np.arange(max(premiere, 0))')], "sans purge"),
    (M, [('return np.arange(max(premiere - purge, 0))', 'return np.arange(max(premiere + 1, 0))')],
     "la première séance notée apprise"),
    (M, [('dates.searchsorted(b, side="right"))', 'dates.searchsorted(b, side="left"))')],
     "la dernière séance de l'année sans prévision"),
    (M, [('ok = np.isfinite(Xa).all(axis=1) & np.isfinite(ra)', 'ok = np.isfinite(Xa).all(axis=1)')],
     "apprendre sur des réponses vides"),
    (M, [('return Xa[ok], reponses(ra[ok]).astype(int), ok', 'return Xa[ok], (ra[ok] >= 0).astype(int), ok')],
     "un rendement nul appris comme une hausse"),
    (M, [('    echelle = StandardScaler().fit(Xa)\n    with _sans_faux_avertissements():\n        modele = LogisticRegression',
          '    echelle = StandardScaler().fit(np.vstack([Xa, Xt]))\n    with _sans_faux_avertissements():\n        modele = LogisticRegression')],
     "le centrage voit l'année notée"),
    (M, [('p = expit((echelle.transform(Xt) * modele.coef_[0])', 'p = expit((echelle.transform(Xt) * -modele.coef_[0])')],
     "le signe de c inversé"),
    (M, [('p = expit((echelle.transform(Xt) * modele.coef_[0]).sum(axis=1) + modele.intercept_[0])',
          'p = expit((echelle.transform(Xt) * modele.coef_[0]).sum(axis=1))')], "c sans sa constante"),
    (M, [('p = modele.predict_proba(X)[:, list(modele.classes_).index(1)]',
          'p = modele.predict_proba(X)[:, list(modele.classes_).index(0)]')], "d donne la probabilité de baisse"),
    (M, [('modele = HistGradientBoostingClassifier(**reglages).fit(Xa, ya)',
          'modele = HistGradientBoostingClassifier().fit(Xa, ya)')], "d avec les réglages par défaut"),
    (M, [('REGLAGES_C = {"C": 1.0, "max_iter": 1000}', 'REGLAGES_C = {"C": 0.5, "max_iter": 1000}')], "c : C = 0,5"),
    (M, [('REGLAGES_D = {"max_iter": 200,', 'REGLAGES_D = {"max_iter": 100,')], "d : 100 itérations"),
    (M, [('pc[test], pd_[test] = c_t.reshape(len(test), v), d_t.reshape(len(test), v)',
          'pc[test], pd_[test] = d_t.reshape(len(test), v), c_t.reshape(len(test), v)')], "c et d échangées"),
    (M, [('s["c"], s["d"], appris = apprendre_et_prevoir', 's["d"], s["c"], appris = apprendre_et_prevoir')],
     "c et d échangées dans les sorties"),
    (M, [('(dates[appr][lignes_ok][0], dates[appr][lignes_ok][-1], int(ok.sum()))',
          '(dates[appr][lignes_ok][0], dates[appr][lignes_ok][-1], int(len(ok)))')], "le nombre de lignes apprises"),
    # Les avis.
    (M, [('(x > SEUILS[m]).astype(float)', '(x >= SEUILS[m]).astype(float)')], "0 ou 50 % comptés « hausse »"),
    (M, [('return np.where(np.isfinite(x), (x > SEUILS[m]).astype(float), np.nan)', 'return (x > SEUILS[m]).astype(float)')],
     "pas d'avis compté comme « baisse »"),
    (M, [('SEUIL_PROBA = 0.5 ', 'SEUIL_PROBA = 0.45 ')], "c et d disent « hausse » dès 45 %"),
    (M, [('SORTIE = {"a": "force", "b": "mom12_1", "hier": "r1"}', 'SORTIE = {"a": "mom12_1", "b": "mom12_1", "hier": "r1"}')],
     "a dit le momentum"),
    (M, [('SORTIE = {"a": "force", "b": "mom12_1", "hier": "r1"}', 'SORTIE = {"a": "force", "b": "mom12_1", "hier": "r5"}')],
     "« comme hier » sur 5 séances"),
    # Le critère 1.
    (M, [('bonnes = 1 - mm - yy + 2 * my', 'bonnes = 1 - mm - yy + my')], "bonnes réponses fausses"),
    (M, [('tirage = mm * yy + (1 - mm) * (1 - yy)', 'tirage = mm * yy')], "le tirage au sort faux"),
    (M, [('"passe": face["tirage"]["vu"] and face["monte"]["vu"]', '"passe": face["tirage"]["vu"] or face["monte"]["vu"]')],
     "le « ET » du critère 1 devenu « OU »"),
    (M, [('"vu": bool(d > seuil)', '"vu": bool(d >= seuil)')], "« vu » à égalité"),
    (M, [('"barre": float(max(tirage', '"barre": float(min(tirage')], "la barre trop basse"),
    (M, [('"vu_dessous": bool(-d > banc.seuil_critique(-d, -t, niveau))',
          '"vu_dessous": bool(-d > banc.seuil_critique(-d, t, niveau))')], "« nettement en dessous » faux"),
    (M, [('poids = banc.poids_blocs(int(p.sum()), bloc, tirages, graine + i)',
          'poids = banc.poids_blocs(int(p.sum()), bloc, tirages, graine)')], "les mêmes blocs pour toutes les périodes"),
    (M, [('sens_du_jour=(hier["face"]["tirage"]["ecart"], t_hier), niveau=niveau)[0]',
          'sens_du_jour=(hier["face"]["tirage"]["ecart"], t_hier), niveau=0.95)[0]')], "le critère à 95 %"),
    (M, [('face["sens_du_jour"] = _face(d_t - abs(d_h), np.asarray(t_t) - np.abs(t_h), niveau)',
          'face["sens_du_jour"] = _face(d_t - d_h, np.asarray(t_t) - t_h, niveau)')],
     "le sens du jour sans « dans un sens ou dans l'autre »"),
    (M, [('face["sens_du_jour"] = _face(d_t - abs(d_h), np.asarray(t_t) - np.abs(t_h), niveau)',
          'face["sens_du_jour"] = _face(d_t - abs(d_h), np.asarray(t_t) - abs(d_h), niveau)')],
     "le sens du jour sans ses tirages (pas apparié)"),
    (M, [('hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], poids, niveau=niveau)',
          'hier, t_hier = critere_1(np.nan_to_num(av["a"][p]), y[p], valide[p], poids, niveau=niveau)')],
     "« comme hier » remplacé par a"),
    (M, [('hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], poids, niveau=niveau)',
          'hier, t_hier = critere_1(np.nan_to_num(av["hier"][p]), y[p], valide[p], banc.poids_blocs(int(p.sum()), bloc, tirages, graine + 99), niveau=niveau)')],
     "« comme hier » sur d'autres tirages des blocs"),
    (M, [('qualif = ("elle fait nettement mieux que le sens du jour" if f["vu"] else',
          'qualif = ("elle fait nettement mieux que le sens du jour" if f["vu_dessous"] else')],
     "« nettement mieux » au lieu de « moins bien »"),
    (M, [('passent = [m for m in CANDIDATES if resultats_tout[m]["passe"]]',
          'passent = [m for m in METHODES if resultats_tout[m]["passe"]]')], "« comme hier » ouvre la porte A"),
    (M, [('valide = a_un_lendemain & np.logical_and.reduce([np.isfinite(av[m]) for m in METHODES])',
          'valide = a_un_lendemain')], "une valeur-jour sans avis comptée"),
    (M, [('"retirees": int((a_un_lendemain & ~valide).sum())', '"retirees": 0')], "les retirées pas comptées"),
    (M, [('lignes = np.flatnonzero(dans & np.isfinite(donnees.r).any(axis=1))',
          'lignes = np.flatnonzero(dans & np.isfinite(donnees.r).all(axis=1))')], "une séance à valeur manquante retirée"),
    (M, [('    y = reponses(r)\n', '    y = (np.where(a_un_lendemain, r, 0.0) >= 0).astype(float)\n')],
     "un rendement nul noté comme une hausse"),
    (M, [('GRAINE = 20261120 ', 'GRAINE = 20261119 ')], "une autre graine des blocs"),
    (M, [('PURGE = 1 ', 'PURGE = 0 ')], "purge à 0 séance"),
    # La mesure et son écriture.
    (M, [('    if not egal_etape0(dossier, donnees, annees):', '    if egal_etape0(dossier, donnees, annees):')],
     "garde-fou de l'étape 0 inversé"),
    (M, [('donnees.r = np.where(np.isfinite(donnees.r), rng.normal(0.0005, 0.02, donnees.r.shape), np.nan)',
          'donnees.r = donnees.r')], "à blanc avec les vraies réponses"),
    (M, [('        dire("À BLANC : les rendements', '        dire("À  blanc : les rendements')], "à blanc pas annoncé"),
    (M, [('    if not a_blanc and os.path.exists(chemin):', '    if False and os.path.exists(chemin):')],
     "la mesure se refait au lieu de réafficher"),
    (M, [('    if not a_blanc:\n        ecrire_mesure(', '    if True:\n        ecrire_mesure(')], "à blanc écrit"),
    (M, [('    if os.path.exists(chemin):\n        raise FileExistsError', '    if False:\n        raise FileExistsError'),
         ('        os.link(temp, chemin)                       # refusé', '        os.replace(temp, chemin)                       # refusé')],
     "une mesure écrase la première"),
    (M, [('return float(x) if math.isfinite(x) else None', 'return float(x)')], "NaN dans le JSON"),
    (M, [('if os.path.abspath(dossier_banc).startswith(os.path.abspath(banc.MARCHE)):', 'if False:')],
     "écrire dans data/market permis"),
    (M, [('def charger_cours(dossier=banc.MARCHE, tickers=None, fin=banc.FIN_AVANT_COFFRE):',
          'def charger_cours(dossier=banc.MARCHE, tickers=None, fin=None):')], "le coffre lu par défaut"),
    # Le rapport.
    (M, [('verdict = ("ne concourt pas" if m not in CANDIDATES else "PASSE" if x["passe"] else "ne passe pas")',
          'verdict = ("ne concourt pas" if m not in CANDIDATES else "ne passe pas" if x["passe"] else "PASSE")')],
     "le verdict inversé"),
    (M, [('if x["face"]["tirage"]["vu"] and not x["face"]["monte"]["vu"]:', 'if x["face"]["tirage"]["vu"] or not x["face"]["monte"]["vu"]:')],
     "la phrase « elle trie mieux » hors de son cas"),
    (M, [('elle = "elle" if len(rap["passent"]) == 1 else "elles"', 'elle = "elle"')], "« pour elle » au pluriel"),
    (M, [('    if h["vu"]:\n', '    if h["vu_dessous"]:\n'), ('    elif h["vu_dessous"]:\n', '    elif h["vu"]:\n')],
     "élan et rebond échangés"),
    (M, [('return _ecart(x, dec) + (" point" if abs(v) < 2 else " points")', 'return _ecart(x, dec) + (" point" if abs(v) <= 2 else " points")')],
     "« 2,00 point » au singulier"),
    (M, [('il lui fallait plus de {banc._pct(x[\'barre\'], 2)}', 'il lui fallait plus de {banc._pct(x[\'bonnes\'], 2)}')],
     "la barre remplacée par ses bonnes réponses"),
    # L'étape 0, ce que l'étape 1 partage avec elle.
    (B, [('if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is not None:', 'if False:')],
     "dates illisibles ou avec fuseau acceptées"),
    (B, [('if not isinstance(df.index, pd.DatetimeIndex) or df.index.tz is not None:',
          'if not isinstance(df.index, pd.DatetimeIndex):')], "dates avec fuseau acceptées"),
    (B, [('    for colonne in colonnes:\n        if colonne not in df.columns:', '    for colonne in ():\n        if colonne not in df.columns:')],
     "colonne absente pas signalée"),
    (B, [('if (np.isinf(x) | (x <= 0)).any():', 'if (np.isinf(x) | (x < 0)).any():')], "un 0 accepté"),
    (B, [('df = df.sort_index().loc[:fin]', 'df = df.sort_index()')], "la coupe du coffre retirée"),
    (B, [('pos = change.index.searchsorted(jours, side="right")', 'pos = change.index.searchsorted(jours, side="left")')],
     "l'euro/dollar du jour même"),
    (B, [('    return prix.shift(-1) / prix - 1', '    return prix / prix.shift(1) - 1')], "la réponse = le rendement du jour"),
    (B, [('    h = ecart / 2', '    h = ecart')], "l'écart compté deux fois"),
    (B, [('res = (2 * (my - mm * yy), 1 - mm - 2 * yy + 2 * my)', 'res = ((my - mm * yy), 1 - mm - 2 * yy + 2 * my)')],
     "l'écart face au tirage divisé par 2"),
    (B, [('return float(np.quantile(observe - np.asarray(tires, float), niveau))',
          'return float(np.quantile(observe - np.asarray(tires, float), 1 - niveau))')], "le seuil du mauvais côté"),
    (B, [('idx = ((debuts[:, :, None] + np.arange(bloc)) % n)', 'idx = ((debuts[:, :, None] + np.zeros(bloc, int)) % n)')],
     "des blocs d'une seule séance répétée"),
]


def essayer(numero, fichier, changements, verbose=False):
    """Une erreur glissée dans une copie ; « attrapée » si les vérifications échouent."""
    with tempfile.TemporaryDirectory(prefix="mutation-banc-") as dossier:
        for nom in COPIES:
            shutil.copy(os.path.join(PROJET, nom), dossier)
        for nom in LIENS:
            os.symlink(os.path.join(PROJET, nom), os.path.join(dossier, nom))
        if fichier is not None:
            chemin = os.path.join(dossier, fichier)
            texte = open(chemin, encoding="utf-8").read()
            for avant, apres in changements:
                if texte.count(avant) != 1:
                    return numero, "INTROUVABLE", f"« {avant[:60]} » apparaît {texte.count(avant)} fois"
                texte = texte.replace(avant, apres)
            with open(chemin, "w", encoding="utf-8") as f:
                f.write(texte)
        env = {**os.environ, "OMP_NUM_THREADS": "2", "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            r = subprocess.run([sys.executable, "banc.py", "--verifier"], cwd=dossier, capture_output=True,
                               text=True, timeout=1200, env=env)
        except subprocess.TimeoutExpired:
            return numero, "attrapée", "trop long (plus de 20 min)"
        rates = [l[2:] for l in r.stdout.splitlines() if l.startswith("✗")]
        if r.returncode == 0:
            return numero, "PASSE", ""
        if rates:
            return numero, "attrapée", f"{len(rates)} échec(s), dont : {rates[0][:110]}"
        derniere = (r.stderr.strip().splitlines() or ["?"])[-1]
        return numero, "attrapée", f"plantage : {derniere[:110]}"


def main():
    choix = [int(x) for x in sys.argv[1:]] or list(range(1, len(ERREURS) + 1))
    debut = time.time()
    print("Témoin (aucune erreur) : les vérifications doivent passer…", flush=True)
    _, etat, detail = essayer(0, None, [])
    if etat != "PASSE":
        sys.exit(f"Le banc sans erreur ne passe pas ses vérifications ({detail}) : rien à mesurer.")
    print(f"   oui ({time.time() - debut:.0f} s).\n", flush=True)
    resultats = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futurs = {pool.submit(essayer, i, *ERREURS[i - 1][:2]): i for i in choix}
        for futur in concurrent.futures.as_completed(futurs):
            numero, etat, detail = futur.result()
            resultats[numero] = (etat, detail)
            print(f"{numero:3d}. {etat:11} {ERREURS[numero - 1][2]} — {detail}", flush=True)
    passent = [i for i, (e, _) in resultats.items() if e != "attrapée"]
    print(f"\n{len(choix) - len(passent)} erreurs volontaires attrapées sur {len(choix)} "
          f"({(time.time() - debut) / 60:.1f} min).")
    for i in sorted(passent):
        print(f"   {resultats[i][0]} : n° {i}, {ERREURS[i - 1][2]} {resultats[i][1]}")
    sys.exit(0 if not passent else 1)


if __name__ == "__main__":
    main()
